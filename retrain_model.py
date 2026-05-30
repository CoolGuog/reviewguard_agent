"""
ReviewGuard 模型重训练脚本 V3
解决模型对正常评论误判率极高的问题

改进:
1. Focal Loss 解决类别不平衡
2. 类别权重加权
3. 更严格早停（基于Val F1）
4. 最优阈值搜索（不固定0.5）
5. 更强的正则化（Dropout + weight_decay）

特征说明:
- 16维元数据特征，仅依赖 comment_time + username + text
- 不依赖 order_time（槽位0保留填0，兼容旧模型）
- 时间聚集检测由 BurstDetector 在推理后处理，不参与模型训练

用法:
  python retrain_model.py
  python retrain_model.py --epochs 30 --lr 1e-5
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
from datetime import datetime

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.metrics import classification_report, f1_score, confusion_matrix
from sklearn.preprocessing import StandardScaler
import joblib

# ============================================================
# 配置
# ============================================================
# 优先用本地缓存
_LOCAL_CANDIDATES = [
    r"D:\huggingface_cache\models--hfl--chinese-roberta-wwm-ext\snapshots\5c58d0b8ec1d9014354d691c538661bf00bfdb44",
    os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub",
                 "models--hfl--chinese-roberta-wwm-ext", "snapshots",
                 "5c58d0b8ec1d9014354d691c538661bf00bfdb44"),
]
BERT_MODEL_PATH = os.environ.get("BERT_MODEL_PATH")
if not BERT_MODEL_PATH:
    for candidate in _LOCAL_CANDIDATES:
        if os.path.exists(candidate):
            BERT_MODEL_PATH = candidate
            break
    if not BERT_MODEL_PATH:
        BERT_MODEL_PATH = "hfl/chinese-roberta-wwm-ext"
DATA_DIR = os.environ.get(
    "REVIEWGUARD_DATA_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
)
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "retrained_model")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================
# 数据集
# ============================================================
class ReviewDataset(Dataset):
    def __init__(self, texts, labels, meta_features, tokenizer, max_len=128):
        self.texts = texts
        self.labels = labels
        self.meta_features = meta_features
        self.tokenizer = tokenizer
        self.max_len = max_len

    def __len__(self):
        return len(self.texts)

    def __getitem__(self, idx):
        text = str(self.texts[idx])
        label = self.labels[idx]
        meta = self.meta_features[idx]

        encoding = self.tokenizer(
            text,
            max_length=self.max_len,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )

        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "meta": torch.tensor(meta, dtype=torch.float),
            "label": torch.tensor(label, dtype=torch.long),
        }


# ============================================================
# 模型
# ============================================================
class GeoPoisonDetector(nn.Module):
    def __init__(self, bert_path, meta_dim=16, freeze_layers=8):
        super().__init__()
        from transformers import BertModel
        self.bert = BertModel.from_pretrained(bert_path)

        # 冻结前N层
        if freeze_layers > 0:
            for param in self.bert.embeddings.parameters():
                param.requires_grad = False
            for i, layer in enumerate(self.bert.encoder.layer):
                if i < freeze_layers:
                    for param in layer.parameters():
                        param.requires_grad = False

        hidden_size = self.bert.config.hidden_size  # 768

        self.classifier = nn.Sequential(
            nn.Linear(hidden_size + meta_dim, 256),
            nn.Dropout(0.3),
            nn.ReLU(),
            nn.Linear(256, 64),
            nn.Dropout(0.2),
            nn.ReLU(),
            nn.Linear(64, 2),
        )

    def forward(self, input_ids, attention_mask, meta):
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        cls_output = outputs.last_hidden_state[:, 0, :]
        combined = torch.cat([cls_output, meta], dim=1)
        logits = self.classifier(combined)
        return logits


# ============================================================
# ============================================================
# 特征提取（与 detection_model.py 保持完全一致）
# ============================================================
def extract_meta_features(df, scaler=None, fit=False):
    """
    提取16维元数据特征（与 detection_model.py 的 extract_meta_features 完全对齐）

    特征布局：
    [0] time_diff_hours: 保留位，填0（兼容旧模型，不再依赖order_time）
    [1] comment_hour: 评论小时 (0-23, 默认12)
    [2] is_late_night: 是否凌晨 0-6点 (0/1)
    [3] is_work_hour: 是否工作时间 9-18点 (0/1)
    [4] is_generic_username: 匹配 ^user\d+$ (0/1)
    [5] is_short_generic: 匹配 ^u\d+$ (0/1)
    [6] username_len: 用户名长度
    [7] username_digit_ratio: 用户名数字比例
    [8] text_length: 评论文本长度
    [9] exclamation_count: 感叹号数量
    [10] question_count: 问号数量
    [11] has_specific_detail: 是否含具体细节 (0/1)
    [12] has_personal_exp: 是否含个人体验词 (0/1)
    [13] sentence_count: 句子数
    [14] avg_sentence_len: 平均句长
    [15] punct_density: 标点密度
    """
    import re

    # 预处理 comment_time 为 datetime
    if 'comment_time' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['comment_time']):
            df['comment_time'] = pd.to_datetime(df['comment_time'], errors='coerce')
    else:
        df['comment_time'] = pd.NaT

    feats = pd.DataFrame(index=df.index)

    # 槽位0: 保留位（原 time_diff_hours），填0
    feats['time_diff_hours'] = 0.0

    # 时间特征 3维（仅依赖 comment_time）
    feats['comment_hour'] = df['comment_time'].dt.hour.fillna(12).astype(float)
    feats['is_late_night'] = (feats['comment_hour'].between(0, 6)).astype(int)
    feats['is_work_hour'] = (feats['comment_hour'].between(9, 18)).astype(int)

    # 用户名特征 4维
    username_col = df.get('username', pd.Series([''] * len(df), index=df.index))
    feats['is_generic_username'] = username_col.apply(
        lambda x: 1 if re.match(r'^user\d+$', str(x).strip()) else 0)
    feats['is_short_generic'] = username_col.apply(
        lambda x: 1 if re.match(r'^u\d+$', str(x).strip()) else 0)
    feats['username_len'] = username_col.apply(lambda x: len(str(x)))
    feats['username_digit_ratio'] = username_col.apply(
        lambda x: sum(c.isdigit() for c in str(x)) / max(len(str(x)), 1))

    # 文本统计特征 8维
    text_col = df.get('text', df.get('content', pd.Series([''] * len(df), index=df.index)))
    text_col = text_col.astype(str)
    feats['text_length'] = text_col.apply(len)
    feats['exclamation_count'] = text_col.apply(
        lambda x: x.count('！') + x.count('!'))
    feats['question_count'] = text_col.apply(
        lambda x: x.count('？') + x.count('?'))
    feats['has_specific_detail'] = text_col.apply(
        lambda x: 1 if re.search(
            r'\d+[GgMm][Bb]|\d+%|\d+小时|\d+天|\d+分钟|序列号|开机\d+', x
        ) else 0)
    feats['has_personal_exp'] = text_col.apply(
        lambda x: 1 if any(w in x for w in [
            '用了', '感觉', '我发现',
            '亲测', '我买的', '拿到手', '入手'
        ]) else 0)
    feats['sentence_count'] = text_col.apply(
        lambda x: max(1, len([s for s in re.split(r'[。！？!?.;；；]', x) if s.strip()])))
    feats['avg_sentence_len'] = feats['text_length'] / feats['sentence_count']
    feats['punct_density'] = text_col.apply(
        lambda x: sum(1 for c in x if c in (
            '！？。，、；：“”‘’…—,.!?;:\''
        )) / max(len(x), 1))

    features = feats.values.astype(np.float32)

    if fit:
        scaler = StandardScaler()
        features = scaler.fit_transform(features)
    else:
        features = scaler.transform(features)

    return features, scaler

# ============================================================
# Focal Loss
# ============================================================
class FocalLoss(nn.Module):
    def __init__(self, alpha=None, gamma=2.0, reduction='mean'):
        super().__init__()
        self.gamma = gamma
        self.alpha = alpha
        self.reduction = reduction

    def forward(self, inputs, targets):
        ce_loss = nn.CrossEntropyLoss(weight=self.alpha, reduction='none')(inputs, targets)
        pt = torch.exp(-ce_loss)
        focal_loss = ((1 - pt) ** self.gamma) * ce_loss
        if self.reduction == 'mean':
            return focal_loss.mean()
        return focal_loss.sum()


# ============================================================
# 训练
# ============================================================
def train_model(train_loader, val_loader, model, device, epochs=20, lr=2e-5, patience=5):
    # 计算类别权重
    all_labels = []
    for batch in train_loader:
        all_labels.extend(batch["label"].tolist())
    label_counts = np.bincount(all_labels)
    total = len(all_labels)
    class_weights = torch.tensor(
        [total / (2 * c) if c > 0 else 1.0 for c in label_counts],
        dtype=torch.float32
    ).to(device)
    print(f"类别权重: real={class_weights[0]:.2f}, fake={class_weights[1]:.2f}")

    criterion = FocalLoss(alpha=class_weights, gamma=2.0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs)

    best_val_f1 = 0
    patience_counter = 0

    for epoch in range(epochs):
        # 训练
        model.train()
        train_loss = 0
        train_correct = 0
        train_total = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            meta = batch["meta"].to(device)
            labels = batch["label"].to(device)

            optimizer.zero_grad()
            logits = model(input_ids, attention_mask, meta)
            loss = criterion(logits, labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()

            train_loss += loss.item()
            preds = logits.argmax(dim=1)
            train_correct += (preds == labels).sum().item()
            train_total += labels.size(0)

        scheduler.step()

        # 验证
        model.eval()
        val_preds = []
        val_labels = []
        val_loss = 0

        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch["input_ids"].to(device)
                attention_mask = batch["attention_mask"].to(device)
                meta = batch["meta"].to(device)
                labels = batch["label"].to(device)

                logits = model(input_ids, attention_mask, meta)
                loss = criterion(logits, labels)
                val_loss += loss.item()

                preds = logits.argmax(dim=1)
                val_preds.extend(preds.cpu().tolist())
                val_labels.extend(labels.cpu().tolist())

        val_f1 = f1_score(val_labels, val_preds, average='macro')
        val_acc = sum(1 for p, l in zip(val_preds, val_labels) if p == l) / len(val_labels)

        # 分类别准确率
        real_preds = [p for p, l in zip(val_preds, val_labels) if l == 0]
        fake_preds = [p for p, l in zip(val_preds, val_labels) if l == 1]
        real_acc = sum(1 for p in real_preds if p == 0) / max(len(real_preds), 1)
        fake_acc = sum(1 for p in fake_preds if p == 1) / max(len(fake_preds), 1)

        print(f"Epoch {epoch+1}/{epochs} | "
              f"Train Loss: {train_loss/len(train_loader):.4f} Acc: {train_correct/train_total:.4f} | "
              f"Val Loss: {val_loss/len(val_loader):.4f} Acc: {val_acc:.4f} F1: {val_f1:.4f} | "
              f"Real准确率: {real_acc:.2%} Fake准确率: {fake_acc:.2%}")

        if val_f1 > best_val_f1:
            best_val_f1 = val_f1
            patience_counter = 0
            torch.save({
                "model_state_dict": model.state_dict(),
                "val_f1": val_f1,
                "epoch": epoch,
            }, os.path.join(OUTPUT_DIR, "best_model.pt"))
            print(f"  → 新最佳! F1={val_f1:.4f}")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"早停: {patience}轮未提升")
                break

    print(f"\n最佳验证F1: {best_val_f1:.4f}")
    return model


# ============================================================
# 阈值调优
# ============================================================
def evaluate_with_threshold(model, val_loader, device):
    model.eval()
    all_probs = []
    all_labels = []

    with torch.no_grad():
        for batch in val_loader:
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            meta = batch["meta"].to(device)
            labels = batch["label"]

            logits = model(input_ids, attention_mask, meta)
            probs = torch.softmax(logits, dim=1)
            all_probs.extend(probs[:, 1].cpu().tolist())  # fake概率
            all_labels.extend(labels.tolist())

    all_probs = np.array(all_probs)
    all_labels = np.array(all_labels)

    best_threshold = 0.5
    best_f1 = 0

    for threshold in np.arange(0.3, 0.8, 0.02):
        preds = (all_probs >= threshold).astype(int)
        f1 = f1_score(all_labels, preds, average='macro')
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = threshold

    preds = (all_probs >= best_threshold).astype(int)
    report = classification_report(all_labels, preds, target_names=["real", "fake"])
    cm = confusion_matrix(all_labels, preds)

    print(f"\n{'='*60}")
    print(f"最优阈值: {best_threshold:.2f}")
    print(f"{'='*60}")
    print(report)
    print(f"混淆矩阵:\n{cm}")

    # 对比0.5阈值
    preds_05 = (all_probs >= 0.5).astype(int)
    f1_05 = f1_score(all_labels, preds_05, average='macro')
    report_05 = classification_report(all_labels, preds_05, target_names=["real", "fake"])
    print(f"\n--- 对比: 默认阈值0.5, F1={f1_05:.4f} ---")
    print(report_05)

    return best_threshold, best_f1


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="ReviewGuard模型重训练")
    parser.add_argument("--epochs", type=int, default=25)
    parser.add_argument("--batch_size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--freeze", type=int, default=8, help="冻结RoBERTa前N层")
    args = parser.parse_args()

    print("=" * 60)
    print("ReviewGuard 模型重训练 V3")
    print("=" * 60)

    # ---- 加载数据（CSV无表头，手动指定列名） ----
    train_path = os.path.join(DATA_DIR, "train_v2.csv")
    dev_path = os.path.join(DATA_DIR, "dev_v2.csv")
    test_path = os.path.join(DATA_DIR, "test_v2.csv")

    COLUMN_NAMES = ["label", "text", "comment_time", "extra_time", "username", "score"]

    train_df = pd.read_csv(train_path, header=None, names=COLUMN_NAMES)
    dev_df = pd.read_csv(dev_path, header=None, names=COLUMN_NAMES)
    test_df = pd.read_csv(test_path, header=None, names=COLUMN_NAMES)

    # 转换 label 为整数
    for df in [train_df, dev_df, test_df]:
        df["label"] = df["label"].astype(int)
        # 统一时间格式: YYYY/MM/DD → YYYY-MM-DD
        df["comment_time"] = df["comment_time"].astype(str).str.replace("/", "-", regex=False)
        df["extra_time"] = df["extra_time"].astype(str).str.replace("/", "-", regex=False)
        # score 转为数值，非数值填默认 3
        df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(3).astype(int)

    print(f"\n原始数据:")
    print(f"  train: {len(train_df)}条 (label分布: {dict(train_df['label'].value_counts().sort_index())})")
    print(f"  dev:   {len(dev_df)}条 (label分布: {dict(dev_df['label'].value_counts().sort_index())})")
    print(f"  test:  {len(test_df)}条 (label分布: {dict(test_df['label'].value_counts().sort_index())})")

    # 列名检查
    print(f"\n列名: {train_df.columns.tolist()}")
    # 预览
    print(f"样本:\n{train_df.head(2).to_string()}")

    # 统一列名
    for df in [train_df, dev_df, test_df]:
        if "content" in df.columns and "text" not in df.columns:
            df["text"] = df["content"]
        if "nickname" in df.columns and "username" not in df.columns:
            df["username"] = df["nickname"]
        # 确保必要列
        for col, default in [("text", ""), ("username", "unknown"), ("comment_time", "2025-01-01 12:00:00"), ("score", 3)]:
            if col not in df.columns:
                df[col] = default

    # ---- 提取特征 ----
    print("\n提取元数据特征...")

    # 合并所有数据一起fit scaler
    all_df = pd.concat([train_df, dev_df, test_df], ignore_index=True)
    all_meta, scaler = extract_meta_features(all_df, fit=True)
    joblib.dump(scaler, os.path.join(OUTPUT_DIR, "meta_scaler.pkl"))

    # 拆分回去
    n_train = len(train_df)
    n_dev = len(dev_df)
    train_meta = all_meta[:n_train]
    dev_meta = all_meta[n_train:n_train + n_dev]
    test_meta = all_meta[n_train + n_dev:]

    print(f"  特征维度: {train_meta.shape[1]}")

    # ---- Tokenizer ----
    print("\n加载Tokenizer...")
    from transformers import BertTokenizer
    tokenizer = BertTokenizer.from_pretrained(BERT_MODEL_PATH)

    # ---- DataLoader ----
    train_dataset = ReviewDataset(train_df["text"].tolist(), train_df["label"].tolist(), train_meta, tokenizer)
    dev_dataset = ReviewDataset(dev_df["text"].tolist(), dev_df["label"].tolist(), dev_meta, tokenizer)
    test_dataset = ReviewDataset(test_df["text"].tolist(), test_df["label"].tolist(), test_meta, tokenizer)

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True, num_workers=0)
    dev_loader = DataLoader(dev_dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_dataset, batch_size=args.batch_size, shuffle=False, num_workers=0)

    # ---- 模型 ----
    print("\n初始化模型...")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = GeoPoisonDetector(BERT_MODEL_PATH, meta_dim=train_meta.shape[1], freeze_layers=args.freeze).to(device)
    print(f"  设备: {device}")
    print(f"  可训练参数: {sum(p.numel() for p in model.parameters() if p.requires_grad):,}")

    # ---- 训练 ----
    print("\n开始训练...")
    model = train_model(
        train_loader, dev_loader, model, device,
        epochs=args.epochs, lr=args.lr, patience=5,
    )

    # ---- 阈值调优（在dev集上） ----
    print("\n在Dev集上调优阈值...")
    checkpoint = torch.load(
        os.path.join(OUTPUT_DIR, "best_model.pt"),
        map_location=device,
        weights_only=False,  # PyTorch 2.6 兼容：checkpoint 含 numpy 对象
    )
    model.load_state_dict(checkpoint["model_state_dict"])
    best_threshold, _ = evaluate_with_threshold(model, dev_loader, device)

    # ---- 在Test集上最终评估 ----
    print("\n在Test集上最终评估...")
    test_threshold, test_f1 = evaluate_with_threshold(model, test_loader, device)

    # ---- 保存最终模型 ----
    final_model_path = os.path.join(OUTPUT_DIR, "best_geo_poison_detector_v3.pt")
    torch.save({
        "model_state_dict": model.state_dict(),
        "meta_dim": train_meta.shape[1],
        "threshold": best_threshold,
        "test_f1": test_f1,
        "bert_path": BERT_MODEL_PATH,
        "freeze_layers": args.freeze,
        "trained_at": datetime.now().isoformat(),
    }, final_model_path)

    # 保存配置
    config = {
        "threshold": best_threshold,
        "test_f1": test_f1,
        "meta_dim": train_meta.shape[1],
        "bert_path": BERT_MODEL_PATH,
        "freeze_layers": args.freeze,
        "scaler_path": os.path.join(OUTPUT_DIR, "meta_scaler.pkl"),
        "trained_at": datetime.now().isoformat(),
    }
    with open(os.path.join(OUTPUT_DIR, "config.json"), "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"✅ 训练完成!")
    print(f"  Dev最优阈值: {best_threshold:.2f}")
    print(f"  Test F1: {test_f1:.4f}")
    print(f"  模型: {final_model_path}")
    print(f"  缩放器: {os.path.join(OUTPUT_DIR, 'meta_scaler.pkl')}")
    print(f"{'='*60}")

    print("\n更新ReviewGuard使用新模型:")
    print(f"  1. 复制 best_geo_poison_detector_v3.pt → reviewguard_agent/")
    print(f"  2. 复制 meta_scaler.pkl → reviewguard_agent/")
    print(f"  3. 修改 config.py: model_path = 'best_geo_poison_detector_v3.pt'")
    print(f"  4. 修改 detection_model.py: threshold = {best_threshold:.2f}")


if __name__ == "__main__":
    main()
