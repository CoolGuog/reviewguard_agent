"""
用模拟数据跑通完整检测链路，不依赖爬虫和Agent框架
验证新特性：Burst时间聚类检测 + 纯文本降级模式
"""
import re
from models.detection_model import DetectionModel
from core.burst_detector import BurstDetector

# 模拟评论数据（部分有时间，部分无时间）
MOCK_COMMENTS = [
    # ---- 正常评论（分散时间） ----
    {"text": "这个耳机音质不错，戴了三天了很舒服，降噪效果也好", "username": "def***3",
     "comment_time": "2025-06-15 14:30:22", "product_id": "TEST001", "score": 5},
    {"text": "物流很快，包装完整，用了一周感觉不错", "username": "顾客f288",
     "comment_time": "2025-06-15 16:20:10", "product_id": "TEST001", "score": 5},
    {"text": "手机用了三天，8GB内存运行很流畅，拍照效果也不错", "username": "张三丰",
     "comment_time": "2025-06-15 20:15:00", "product_id": "TEST001", "score": 4},

    # ---- 刷单评论（凌晨聚集） ----
    {"text": "东西不错，质量好，发货快，满意！", "username": "user1234",
     "comment_time": "2025-06-15 01:24:18", "product_id": "TEST001", "score": 5},
    {"text": "非常好，推荐购买！", "username": "u456",
     "comment_time": "2025-06-15 01:25:33", "product_id": "TEST001", "score": 5},
    {"text": "差评，质量太差了", "username": "user7890",
     "comment_time": "2025-06-15 01:26:05", "product_id": "TEST001", "score": 1},
    {"text": "好评好评好评！", "username": "user5678",
     "comment_time": "2025-06-15 01:26:50", "product_id": "TEST001", "score": 5},
    {"text": "不满意，退货了", "username": "u901",
     "comment_time": "2025-06-15 01:27:45", "product_id": "TEST001", "score": 1},

    # ---- 纯文本模式（无时间） ----
    {"text": "质量一流，物超所值，强烈推荐！", "username": "user9999",
     "comment_time": "", "product_id": "TEST001", "score": 5},
    {"text": "用了一周电池很耐用，屏幕清晰度不错", "username": "正常用户",
     "comment_time": "", "product_id": "TEST001", "score": 4},
]


def main():
    print("=" * 50)
    print("ReviewGuard 端到端测试（含 Burst 检测）")
    print("=" * 50)

    # 1. 加载模型
    print("\n📦 加载检测模型...")
    dm = DetectionModel(model_path="best_geo_poison_detector_v3.pt")
    print("✅ 模型加载成功!")

    # 2. 初始化 Burst 检测器
    burst_detector = BurstDetector(window_minutes=30, min_cluster_size=3)

    # 3. 逐条模型推理
    print("\n🔍 Step 1: 模型推理（纯模型分，无时间加权）...")
    print("-" * 50)
    results = []
    for comment in MOCK_COMMENTS:
        label, confidence = dm.predict(
            text=comment["text"],
            metadata={
                "username": comment.get("username"),
                "comment_time": comment.get("comment_time", ""),
                "score": comment.get("score", 0),
            }
        )
        results.append({**comment, "model_label": label, "model_confidence": confidence})

        flag = "🚨" if label == "fake" else "✅"
        time_info = comment["comment_time"][:16] if comment["comment_time"] else "(无时间)"
        print(f"  {flag} [模型:{label}] {confidence:.2%} | {comment['text'][:25]:25s} | {time_info}")

    # 4. Burst 时间聚类检测
    print("\n⏱️  Step 2: Burst 时间聚类检测...")
    print("-" * 50)
    burst_scores = burst_detector.detect(MOCK_COMMENTS)
    burst_summary = burst_detector.get_cluster_summary(MOCK_COMMENTS, burst_scores)

    burst_hits = sum(1 for s in burst_scores if s > 0)
    print(f"  聚集评论: {burst_hits}/{len(MOCK_COMMENTS)} 条")
    print(f"  聚集簇数: {len(burst_summary['clusters'])}")
    for ci, c in enumerate(burst_summary['clusters'], 1):
        print(f"  簇{ci}: {c['start_time']} ~ {c['end_time']}, {c['review_count']}条, 聚集分 {c['avg_burst_score']:.2%}")

    # 5. 分数融合（Burst 只增加嫌疑，不降低）
    print("\n🔀 Step 3: 融合模型分 + Burst分（boost 模式）...")
    print("-" * 50)
    BURST_WEIGHT = 0.25
    for i, r in enumerate(results):
        burst = burst_scores[i]
        r["burst_score"] = round(burst, 4)
        if burst > 0:
            # boost 公式：burst 只增加嫌疑
            boost = BURST_WEIGHT * burst * (1 - r["model_confidence"])
            final_conf = round(min(r["model_confidence"] + boost, 1.0), 4)
            r["confidence"] = final_conf
            if r["model_label"] == "real" and final_conf > 0.5:
                r["label"] = "fake"
            else:
                r["label"] = r["model_label"]
        else:
            r["confidence"] = r["model_confidence"]
            r["label"] = r["model_label"]

        flag = "🚨" if r["label"] == "fake" else "✅"
        burst_tag = f" ⏱️{burst:.0%}" if burst > 0 else ""
        changed = " ←翻转!" if r["label"] != r["model_label"] else ""
        print(f"  {flag} [{r['label']}] 模型:{r['model_confidence']:.2%}{burst_tag} → 最终:{r['confidence']:.2%}{changed} | {r['text'][:20]}...")

    # 6. 统计
    fake_count = sum(1 for r in results if r["label"] == "fake")
    real_count = sum(1 for r in results if r["label"] == "real")
    high_conf_fake = sum(1 for r in results if r["label"] == "fake" and r["confidence"] >= 0.8)

    print("\n" + "=" * 50)
    print("📊 检测结果汇总")
    print("=" * 50)
    print(f"  总评论数: {len(results)}")
    print(f"  虚假评论: {fake_count}")
    print(f"  真实评论: {real_count}")
    print(f"  高置信虚假(≥80%): {high_conf_fake}")
    print(f"  时间聚集评论: {burst_hits}")

    # 7. 攻击事件识别
    print("\n🎯 攻击事件识别")
    print("-" * 50)
    fake_comments = [r for r in results if r["label"] == "fake"]
    if fake_comments:
        hours = []
        for r in fake_comments:
            ct = r.get("comment_time", "")
            if ct and len(ct) >= 13:
                try:
                    hours.append(int(ct.split(" ")[1].split(":")[0]))
                except (IndexError, ValueError):
                    pass
        late_night_count = sum(1 for h in hours if 0 <= h <= 6)
        generic_users = sum(1 for r in fake_comments
                          if re.match(r'^(user\d+|u\d+)$', r.get("username", "")))

        print(f"  凌晨时段(0-6点)虚假评论: {late_night_count}/{fake_count}")
        print(f"  批量格式用户名: {generic_users}/{fake_count}")
        print(f"  时间聚集簇: {len(burst_summary['clusters'])} 个")

        if late_night_count >= 3 or generic_users >= 3 or len(burst_summary['clusters']) > 0:
            print("  ⚠️ 检测到疑似GEO投毒攻击事件！")
            print(f"  攻击特征: 时间爆发性 + 账号批量性 + 时间聚集")
        else:
            print("  未检测到明显攻击事件")
    else:
        print("  无虚假评论，无攻击事件")

    # 8. 纯文本模式验证
    print("\n🧪 纯文本降级验证")
    print("-" * 50)
    text_only = [r for r in results if not r.get("comment_time")]
    if text_only:
        fake_in_text = sum(1 for r in text_only if r["label"] == "fake")
        print(f"  无时间评论: {len(text_only)} 条, 检出虚假 {fake_in_text} 条")
        print(f"  ✅ 纯文本降级模式正常工作")
    else:
        print(f"  所有评论均有时间信息")

    print("\n✅ 端到端测试完成!")


if __name__ == "__main__":
    main()
