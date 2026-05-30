"""
检测模型适配层 — 接入RoBERTa + 元数据特征（仅需 comment_time，不再依赖 order_time）
"""
import os
import logging
import re

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import joblib
from transformers import BertTokenizer, BertModel

logger = logging.getLogger("ReviewGuard.Model")

# 优先用本地缓存（国内免翻墙），否则从HuggingFace下载
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
MAX_LEN = 128
TEMPERATURE = 1.0  # 1.0=标准softmax，值越高概率越平滑（越难区分真假）


class GeoPoisonDetector(nn.Module):
    """RoBERTa + 元数据特征融合的虚假评论检测模型（与 retrain_model.py 架构一致）"""

    def __init__(self, bert_path='hfl/chinese-roberta-wwm-ext', meta_dim=0, freeze_layers=8):
        super().__init__()
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

    def forward(self, input_ids, attention_mask, meta=None):
        outputs = self.bert(input_ids=input_ids, attention_mask=attention_mask)
        cls_output = outputs.last_hidden_state[:, 0, :]
        combined = torch.cat([cls_output, meta], dim=1) if meta is not None else cls_output
        return self.classifier(combined)


def extract_meta_features(df):
    """
    提取16维元数据特征

    注意：仅依赖 comment_time，不依赖 order_time。
    特征槽位0为保留位（原time_diff_hours），填0以兼容旧模型。
    当 comment_time 无效时，时间特征自动填默认值（纯文本模式）。
    """
    features = pd.DataFrame(index=df.index)

    # 解析 comment_time 为 datetime（若列为空或不存在则填 NaT）
    if 'comment_time' in df.columns:
        if not pd.api.types.is_datetime64_any_dtype(df['comment_time']):
            df['comment_time'] = pd.to_datetime(df['comment_time'], errors='coerce')
    else:
        df['comment_time'] = pd.NaT

    # 时间特征 4维（仅依赖 comment_time）
    # 槽位0: 保留位，原为 time_diff_hours（依赖order_time），现填0以兼容旧模型
    features['time_diff_hours'] = 0.0
    features['comment_hour'] = df['comment_time'].dt.hour.fillna(12).astype(float)
    features['is_late_night'] = (features['comment_hour'].between(0, 6)).astype(int)
    features['is_work_hour'] = (features['comment_hour'].between(9, 18)).astype(int)

    # 用户名特征 4维
    features['is_generic_username'] = df['username'].apply(
        lambda x: 1 if re.match(r'^user\d+$', str(x).strip()) else 0)
    features['is_short_generic'] = df['username'].apply(
        lambda x: 1 if re.match(r'^u\d+$', str(x).strip()) else 0)
    features['username_len'] = df['username'].apply(lambda x: len(str(x)))
    features['username_digit_ratio'] = df['username'].apply(
        lambda x: sum(c.isdigit() for c in str(x)) / max(len(str(x)), 1))

    # 文本统计特征 8维
    comment_col = df['comment'].astype(str)
    features['text_length'] = comment_col.apply(len)
    features['exclamation_count'] = comment_col.apply(
        lambda x: x.count('\uff01') + x.count('!'))
    features['question_count'] = comment_col.apply(
        lambda x: x.count('\uff1f') + x.count('?'))
    features['has_specific_detail'] = comment_col.apply(
        lambda x: 1 if re.search(r'\d+[GgMm][Bb]|\d+%|\d+\u5c0f\u65f6|\d+\u5929|\d+\u5206\u949f|\u5e8f\u5217\u53f7|\u5f00\u673a\d+', x) else 0)
    features['has_personal_exp'] = comment_col.apply(
        lambda x: 1 if any(w in x for w in ['\u7528\u4e86', '\u611f\u89c9', '\u6211\u53d1\u73b0', '\u4eb2\u6d4b', '\u6211\u4e70\u7684', '\u62ff\u5230\u624b', '\u5165\u624b']) else 0)
    features['sentence_count'] = comment_col.apply(
        lambda x: max(1, len([s for s in re.split(r'[\u3002\uff01\uff1f!?.;\uff1b\uff1b]', x) if s.strip()])))
    features['avg_sentence_len'] = features['text_length'] / features['sentence_count']
    features['punct_density'] = comment_col.apply(
        lambda x: sum(1 for c in x if c in '\uff01\uff1f\u3002\uff0c\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\u2026\u2014,.!?;:\'') / max(len(x), 1))

    return features


class DetectionModel:
    """检测模型的统一接口，已接入RoBERTa检测器"""

    def __init__(self, model_path: str = None, device: str = None, threshold: float = None):
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.tokenizer = None
        self.scaler = None
        self.threshold = threshold  # None = 从 checkpoint 读取

        if model_path:
            self._load_model(model_path)

    def _load_model(self, model_path: str):
        """加载RoBERTa检测模型 + 特征归一化器"""
        logger.info(f"加载检测模型: {model_path}, 设备: {self.device}")

        self.tokenizer = BertTokenizer.from_pretrained(BERT_MODEL_PATH)

        # 归一化器默认放在模型同目录下（v3模型对应 meta_scaler.pkl）
        scaler_path = os.path.join(
            os.path.dirname(model_path) or ".",
            "meta_scaler.pkl"
        )
        self.scaler = joblib.load(scaler_path)

        self.model = GeoPoisonDetector(
            bert_path=BERT_MODEL_PATH, meta_dim=16, freeze_layers=8
        ).to(self.device)

        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)

        # 兼容两种保存格式：纯 state_dict 或带元数据的 dict
        if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
            state_dict = checkpoint["model_state_dict"]
            saved_threshold = checkpoint.get("threshold")
            logger.info(
                f"加载v3格式checkpoint "
                f"(F1={checkpoint.get('test_f1', '?')}, "
                f"threshold={saved_threshold})"
            )
            # 如果没显式指定阈值，使用 checkpoint 中的最优阈值
            if self.threshold is None and saved_threshold is not None:
                self.threshold = saved_threshold
        else:
            state_dict = checkpoint

        # 仍未设置则使用默认值
        if self.threshold is None:
            self.threshold = 0.5

        self.model.load_state_dict(state_dict)
        self.model.eval()
        logger.info(f"✅ 模型加载完成! 判定阈值: {self.threshold:.2f}")

    def predict(self, text: str, metadata: dict = None) -> tuple:
        """
        预测单条评论是否为虚假评论

        Args:
            text: 评论文本
            metadata: 元数据字典（所有字段可选）:
                - username: 用户名
                - comment_time: 评论时间 (str, 如 "2025-06-15 01:24:18")
                - score: 评分
                - order_time: (已弃用，不再参与检测)

        Returns:
            (label, confidence): label="fake"/"real", confidence=0.0~1.0
        """
        metadata = metadata or {}

        if self.model is not None and self.tokenizer is not None:
            return self._model_predict(text, metadata)

        return self._rule_based_predict(text, metadata)

    def _model_predict(self, text: str, metadata: dict) -> tuple:
        """使用RoBERTa模型推理（纯模型分，不做时间加权）"""
        has_meta = (
            metadata.get("username") is not None
            or metadata.get("comment_time") is not None
        ) and (
            str(metadata.get("username", "")).strip() != ""
            or str(metadata.get("comment_time", "")).strip() != ""
        )

        if has_meta:
            tmp_df = pd.DataFrame([{
                'comment': text,
                'comment_time': metadata.get('comment_time', ''),
                'order_time': '',  # 不再使用 order_time
                'username': metadata.get('username', ''),
            }])
            meta = extract_meta_features(tmp_df)
            meta_scaled = self.scaler.transform(meta.values)
        else:
            meta_scaled = np.zeros((1, 16))

        meta_tensor = torch.tensor(meta_scaled, dtype=torch.float).to(self.device)

        encoding = self.tokenizer(
            text, max_length=MAX_LEN, padding='max_length',
            truncation=True, return_tensors='pt'
        )

        with torch.no_grad():
            logits = self.model(
                encoding['input_ids'].to(self.device),
                encoding['attention_mask'].to(self.device),
                meta_tensor
            )
            prob = torch.softmax(logits / TEMPERATURE, dim=1)

        model_conf = prob[0][1].item()

        # NaN 保护：如果模型输出异常，降级为规则引擎
        if np.isnan(model_conf):
            logger.warning(f"模型输出NaN，降级为规则引擎预测: text={text[:50]}")
            return self._rule_based_predict(text, metadata)

        # 使用可配置的阈值（可来自 checkpoint 或手动指定）
        # 阈值越高 → 假评判定越严格，误判越少但漏检可能增加
        is_fake = model_conf > self.threshold
        # 返回真实的模型概率作为置信度
        confidence = model_conf
        label = "fake" if is_fake else "real"

        return (label, round(confidence, 4))

    def _rule_based_predict(self, text: str, metadata: dict) -> tuple:
        """规则引擎兜底：仅在模型未加载时使用"""
        suspicious_score = 0.0

        if len(text) < 5:
            suspicious_score += 0.3

        if len(text) > 0 and len(set(text)) / len(text) < 0.3:
            suspicious_score += 0.3

        score = metadata.get("score", 3)
        if score in (1, 5):
            suspicious_score += 0.2

        if score == 5 and metadata.get("image_count", 0) == 0:
            suspicious_score += 0.1

        label = "fake" if suspicious_score >= 0.5 else "real"
        confidence = min(suspicious_score + 0.3, 0.99) if label == "fake" else max(1 - suspicious_score, 0.5)

        return (label, round(confidence, 4))
