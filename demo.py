"""
ReviewGuard 完整模拟演示
模拟一次GEO投毒攻击场景：
- 正常商品突然被注入大量虚假评论
- 展示 模型预测 → Burst时间聚类检测 → 分数融合 → 攻击画像 完整流程
- 兼容无时间信息的纯文本检测模式
"""
import re
import json
import time
import random
import os
from datetime import datetime, timedelta
from models.detection_model import DetectionModel
from core.burst_detector import BurstDetector


def generate_normal_comments(n=10):
    """生成正常用户评论（模拟自然时间分布）"""
    templates = [
        ("手机用了一周了，8GB内存运行很流畅，拍照效果也不错，推荐", "李明辉", 5),
        ("耳机降噪效果超出预期，通勤路上终于安静了，续航也够用", "王大山", 5),
        ("物流很快第二天就到了，包装完整，和描述一致", "陈小芳", 4),
        ("手环心率监测比较准，游泳时也能戴，就是屏幕有点小", "赵文博", 4),
        ("键盘手感不错，红轴很安静，连蓝牙也稳定", "周婷婷", 5),
        ("电脑性能不错，跑深度学习模型也没问题，散热一般", "刘志远", 4),
        ("充电宝容量足，能给手机充3次，就是有点重", "孙雅琴", 3),
        ("U盘传输速度很快，64G实际可用59G，质量不错", "吴建国", 5),
        ("第二次买了，上次那个用了两年还在用，质量可靠", "郑晓华", 5),
        ("性价比很高，同价位里配置最好的了，暂时没发现缺点", "黄丽萍", 4),
    ]
    comments = []
    base_time = datetime(2025, 6, 14, 9, 0, 0)
    for i in range(n):
        text, username, score = templates[i % len(templates)]
        # 正常评论分散在48小时内
        comment_time = base_time + timedelta(hours=random.randint(1, 48))
        comments.append({
            "text": text, "username": username, "score": score,
            "comment_time": comment_time.strftime("%Y-%m-%d %H:%M:%S"),
            "product_id": "JD_PHONE_001", "source": "normal",
        })
    return comments


def generate_geo_attack(n=12):
    """模拟GEO投毒攻击：短时间批量假评论，集中在凌晨"""
    # 刷单好评模板
    positive_templates = [
        "东西不错，质量好，发货快，满意！",
        "非常好，强烈推荐！",
        "好评！第二次买了，一如既往的好",
        "很满意，下次还来",
        "品质一流，物超所值",
        "发货快，包装好，好评",
    ]
    # 恶意差评模板
    negative_templates = [
        "差评！质量太差了，大家别买",
        "不好用，后悔买了，浪费钱",
        "假货！和正品完全不一样",
        "质量太差，用三天就坏了，不推荐！",
    ]

    # 批量用户名（机器注册特征）
    fake_usernames = [f"user{random.randint(1000,9999)}" for _ in range(n//2)] + \
                     [f"u{random.randint(100,999)}" for _ in range(n - n//2)]

    # 攻击时间：凌晨2-4点集中爆发（3个簇）
    attack_start = datetime(2025, 6, 15, 2, 0, 0)
    cluster_starts = [0, 12, 45]  # 分钟偏移 — 三个密集簇

    comments = []
    for i in range(n):
        cluster_idx = i % len(cluster_starts)
        cluster_start = attack_start + timedelta(minutes=cluster_starts[cluster_idx])
        # 每个簇内的评论在 2 分钟内密集发布
        comment_time = cluster_start + timedelta(
            seconds=random.randint(5, 120)
        )
        if i < n * 0.6:  # 60%好评刷单
            text = random.choice(positive_templates)
            score = 5
            poison_type = "好评刷单"
        else:  # 40%恶意差评
            text = random.choice(negative_templates)
            score = 1
            poison_type = "恶意差评"

        comments.append({
            "text": text, "username": fake_usernames[i], "score": score,
            "comment_time": comment_time.strftime("%Y-%m-%d %H:%M:%S"),
            "product_id": "JD_PHONE_001", "source": "attack",
            "poison_type": poison_type,
        })
    return comments


def generate_text_only_comments():
    """生成无时间信息的纯文本评论（用于测试纯文本降级模式）"""
    return [
        {"text": "质量不错，第二次买了", "username": "张三", "score": 5,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "normal"},
        {"text": "好评！推荐购买。", "username": "user8888", "score": 5,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "attack"},
        {"text": "非常满意，物流快，包装好，好评好评好评", "username": "u123", "score": 5,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "attack"},
        {"text": "用了一周，电池续航比上代提升不少，屏幕显示效果细腻", "username": "李四", "score": 4,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "normal"},
        {"text": "差评！质量太差了，大家别买！", "username": "user9999", "score": 1,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "attack"},
        {"text": "包装完整，手感不错，就是接口有点少需要转接头", "username": "王五", "score": 4,
         "comment_time": "", "product_id": "JD_TEXT_ONLY", "source": "normal"},
    ]


def analyze_attack_events(results, burst_summary=None):
    """分析攻击事件（含 Burst 指标）"""
    fake_results = [r for r in results if r["label"] == "fake"]
    burst_summary = burst_summary or {}

    if not fake_results:
        return None

    # 时间分析
    fake_times = [r.get("comment_time", "") for r in fake_results if r.get("comment_time")]
    fake_hours = []
    for t in fake_times:
        try:
            fake_hours.append(int(t.split(" ")[1].split(":")[0]))
        except (IndexError, ValueError):
            pass
    late_night = sum(1 for h in fake_hours if 0 <= h <= 6)

    # 用户名分析
    generic_users = sum(1 for r in fake_results
                       if re.match(r'^(user\d+|u\d+)$', r.get("username", "")))

    # 判定攻击
    burst_has_clusters = len(burst_summary.get("clusters", [])) > 0
    burst_reviews = burst_summary.get("reviews_with_burst", 0)

    is_attack = (
        late_night >= 3 or generic_users >= 3 or burst_has_clusters
    )
    confidence = 0
    if late_night >= 3: confidence += 30
    if generic_users >= 3: confidence += 30
    if burst_has_clusters: confidence += 40

    return {
        "is_attack": is_attack,
        "attack_confidence": min(confidence, 100),
        "fake_count": len(fake_results),
        "late_night_fake": late_night,
        "generic_user_fake": generic_users,
        "burst_clusters": len(burst_summary.get("clusters", [])),
        "burst_reviews": burst_reviews,
        "indicators": [],
    }


def generate_report(all_results, attack_info, burst_detector, burst_scores):
    """生成Markdown检测报告（含 Burst 章节）"""
    total = len(all_results)
    fake_count = sum(1 for r in all_results if r["label"] == "fake")
    real_count = total - fake_count
    high_conf_fake = sum(1 for r in all_results
                        if r["label"] == "fake" and r["confidence"] >= 0.8)
    burst_positive = sum(1 for s in burst_scores if s > 0)

    report = f"""# ReviewGuard 检测报告

**检测时间**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**目标商品**: JD_PHONE_001
**评论总数**: {total}

---

## 检测结果概览

| 指标 | 数值 |
|------|------|
| 总评论数 | {total} |
| 虚假评论 | {fake_count} ({fake_count/total*100:.1f}%) |
| 真实评论 | {real_count} ({real_count/total*100:.1f}%) |
| 高置信虚假(≥80%) | {high_conf_fake} |
| 时间聚集评论 | {burst_positive} |

---

## 逐条检测结果

"""
    for i, r in enumerate(all_results, 1):
        flag = "🚨" if r["label"] == "fake" else "✅"
        source_tag = " [攻击]" if r.get("source") == "attack" else " [正常]"
        burst_tag = f" ⏱️聚集:{r.get('burst_score', 0):.0%}" if r.get("burst_score", 0) > 0 else ""
        report += f"{i}. {flag} **{r['label'].upper()}** (置信度 {r['confidence']:.1%}){source_tag}{burst_tag}\n"
        report += f"   - 评论: \"{r['text']}\"\n"
        report += f"   - 用户: @{r['username']} | 评分: {r.get('score', '-')} | 时间: {r['comment_time']}\n\n"

    # ---- Burst 时间聚集章节 ----
    if burst_positive > 0:
        burst_summary = burst_detector.get_cluster_summary(all_results, burst_scores)
        clusters = burst_summary.get("clusters", [])
        report += """---

## ⏱️ 时间聚集检测 (Burst Detection)

**检测方法**: 基于 `comment_time` 的滑动窗口聚类，不需要 `order_time`

"""
        report += f"| 指标 | 数值 |\n|------|------|\n"
        report += f"| 聚集评论数 | {burst_summary['reviews_with_burst']} |\n"
        report += f"| 聚集比例 | {burst_summary['burst_ratio']:.2%} |\n"
        report += f"| 最高聚集分 | {burst_summary['max_burst_score']:.2%} |\n"
        report += f"| 平均聚集分 | {burst_summary['avg_burst_score']:.2%} |\n\n"

        if clusters:
            report += f"**检测到 {len(clusters)} 个时间聚集簇：**\n\n"
            for ci, cluster in enumerate(clusters, 1):
                report += (
                    f"- **簇{ci}**: {cluster['start_time']} ~ {cluster['end_time']}, "
                    f"共 {cluster['review_count']} 条, "
                    f"平均聚集分 {cluster['avg_burst_score']:.2%}\n"
                )
        report += "\n> 时间聚集提示：上述评论在短时间内集中发布，符合批量刷单的行为模式。\n"

    # ---- 攻击事件告警 ----
    if attack_info and attack_info["is_attack"]:
        report += """---

## ⚠️ 攻击事件告警

**告警级别**: 🔴 严重 (CRITICAL)

"""
        report += f"- **攻击置信度**: {attack_info['attack_confidence']}%\n"
        report += f"- **虚假评论数**: {attack_info['fake_count']}\n"
        report += f"- **凌晨时段(0-6点)虚假评论**: {attack_info['late_night_fake']}\n"
        report += f"- **批量格式用户名**: {attack_info['generic_user_fake']}\n"
        if attack_info.get('burst_clusters', 0) > 0:
            report += f"- **时间聚集簇**: {attack_info['burst_clusters']} 个\n"
            report += f"- **聚集评论数**: {attack_info['burst_reviews']}\n"
        report += "\n**攻击特征**: 时间爆发性 + 账号批量性\n\n"
        report += "**建议**: 该商品近期疑似遭受GEO投毒攻击，建议人工复核虚假评论，必要时下架相关商品页面。\n"
    else:
        report += "\n未检测到GEO投毒攻击事件。\n"

    return report


def print_results_table(results, title):
    """格式化打印检测结果"""
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")
    for r in results:
        flag = "🚨" if r["label"] == "fake" else "✅"
        burst_tag = f" ⏱️{r.get('burst_score', 0):.0%}" if r.get("burst_score", 0) > 0 else ""
        source = r.get("source", "")
        tag = ""
        if source == "attack" and r["label"] == "fake":
            tag = " ✓"
        elif source == "attack" and r["label"] == "real":
            tag = " ✗漏检"
        elif source == "normal" and r["label"] == "real":
            tag = " ✓"
        elif source == "normal" and r["label"] == "fake":
            tag = " ✗误判"

        print(f"  {flag} [{r['label']:4s}] 模型:{r.get('model_confidence', r['confidence']):.1%}"
              f"{burst_tag} → 最终:{r['confidence']:.1%}"
              f" | {r['text'][:20]:20s} | @{r['username']:10s}{tag}")


def main():
    print("=" * 60)
    print("  🛡️  ReviewGuard — GEO投毒检测Agent 模拟演示")
    print("  ✨ 新特性: Burst时间聚类检测（无需order_time）")
    print("=" * 60)

    # ================================================================
    # 1. 生成模拟数据
    # ================================================================
    print("\n📡 [Collector] 采集评论数据...")
    normal_comments = generate_normal_comments(10)
    attack_comments = generate_geo_attack(12)
    all_comments = normal_comments + attack_comments
    random.shuffle(all_comments)
    print(f"   共采集 {len(all_comments)} 条评论（含 {len(normal_comments)} 条正常 + {len(attack_comments)} 条攻击注入）")
    print(f"   ℹ️  所有评论均无 order_time，仅依赖 comment_time 或纯文本")

    # ================================================================
    # 2. 加载模型
    # ================================================================
    print("\n🤖 [Detector] 加载检测模型...")
    dm = DetectionModel(model_path="best_geo_poison_detector_v3.pt")
    print("   ✅ 模型加载成功 (CUDA)")

    # ================================================================
    # 3. 初始化 Burst 检测器
    # ================================================================
    print("\n⏱️  [Detector] 初始化 Burst 时间聚类检测器...")
    burst_detector = BurstDetector(window_minutes=30, min_cluster_size=3)
    print(f"   配置: 窗口={burst_detector.window_minutes}分钟, 最小聚集={burst_detector.min_cluster_size}条")

    # ================================================================
    # 4. 逐条模型预测
    # ================================================================
    print("\n🔍 [Detector] Step 1: 逐条模型推理（纯模型分，无时间加权）...")
    print("-" * 60)
    results = []
    for comment in all_comments:
        label, confidence = dm.predict(
            text=comment["text"],
            metadata={
                "username": comment.get("username"),
                "comment_time": comment.get("comment_time", ""),
                "score": comment.get("score", 0),
            }
        )
        results.append({
            **comment,
            "model_label": label,
            "model_confidence": confidence,
            "label": label,
            "confidence": confidence,
        })

    # 打印模型原始结果
    for r in results:
        flag = "🚨" if r["model_label"] == "fake" else "✅"
        source = r.get("source", "")
        print(f"  {flag} [模型:{r['model_label']:4s}] {r['model_confidence']:.1%} | "
              f"{r['text'][:22]:22s} | @{r['username']:10s}")

    # ================================================================
    # 5. Burst 时间聚类检测
    # ================================================================
    print("\n⏱️  [Detector] Step 2: 时间聚类检测（仅用 comment_time）...")
    print("-" * 60)
    burst_scores = burst_detector.detect(all_comments)
    burst_summary = burst_detector.get_cluster_summary(all_comments, burst_scores)

    burst_hits = sum(1 for s in burst_scores if s > 0)
    print(f"   聚集评论: {burst_hits}/{len(all_comments)} 条")
    print(f"   聚集比例: {burst_summary['burst_ratio']:.2%}")
    print(f"   聚集簇数: {len(burst_summary['clusters'])}")
    for ci, cluster in enumerate(burst_summary.get("clusters", []), 1):
        print(f"   簇{ci}: {cluster['start_time']} ~ {cluster['end_time']}, "
              f"{cluster['review_count']}条, 平均聚集分 {cluster['avg_burst_score']:.2%}")

    if burst_hits == 0:
        print("   ℹ️  无时间信息或评论分布均匀，未检测到时间聚集")

    # ================================================================
    # 6. 分数融合
    # ================================================================
    print("\n🔀 [Detector] Step 3: 融合模型分 + Burst分（权重 75:25）...")
    print("-" * 60)
    BURST_WEIGHT = 0.25

    for i, r in enumerate(results):
        burst = burst_scores[i]
        r["burst_score"] = round(burst, 4)
        if burst > 0:
            model_conf = r["model_confidence"]
            final_conf = round((1 - BURST_WEIGHT) * model_conf + BURST_WEIGHT * burst, 4)
            r["confidence"] = final_conf
            if r["model_label"] == "real" and final_conf > 0.5:
                r["label"] = "fake"  # Burst 提升后翻转
            elif r["model_label"] == "fake" and final_conf <= 0.5:
                r["label"] = "real"
            else:
                r["label"] = r["model_label"]

    print_results_table(results, "融合后最终结果")

    # ================================================================
    # 7. 统计
    # ================================================================
    fake_count = sum(1 for r in results if r["label"] == "fake")
    real_count = sum(1 for r in results if r["label"] == "real")

    attack_results = [r for r in results if r.get("source") == "attack"]
    normal_results = [r for r in results if r.get("source") == "normal"]
    attack_detected = sum(1 for r in attack_results if r["label"] == "fake")
    normal_correct = sum(1 for r in normal_results if r["label"] == "real")

    print("\n" + "=" * 60)
    print("📊 [Analyst] 检测结果汇总")
    print("=" * 60)
    print(f"  总评论数: {len(results)}")
    print(f"  判定虚假: {fake_count} | 判定真实: {real_count}")
    if attack_results:
        print(f"  攻击评论检出率: {attack_detected}/{len(attack_results)} = {attack_detected/len(attack_results)*100:.0f}%")
    if normal_results:
        print(f"  正常评论准确率: {normal_correct}/{len(normal_results)} = {normal_correct/len(normal_results)*100:.0f}%")

    # ================================================================
    # 8. 攻击事件识别
    # ================================================================
    print("\n🎯 [Analyst] 攻击事件识别")
    print("-" * 60)
    attack_info = analyze_attack_events(results, burst_summary)
    if attack_info and attack_info["is_attack"]:
        print(f"  ⚠️  检测到GEO投毒攻击事件！置信度: {attack_info['attack_confidence']}%")
        print(f"  凌晨时段虚假评论: {attack_info['late_night_fake']}")
        print(f"  批量格式用户名: {attack_info['generic_user_fake']}")
        if attack_info.get('burst_clusters', 0) > 0:
            print(f"  时间聚集簇: {attack_info['burst_clusters']} 个 | 聚集评论: {attack_info['burst_reviews']} 条")
        print(f"  告警级别: 🔴 严重")
    else:
        print("  未检测到攻击事件")

    # ================================================================
    # 9. 生成报告
    # ================================================================
    print("\n📝 [Reporter] 生成检测报告...")
    report = generate_report(results, attack_info, burst_detector, burst_scores)

    report_path = "data/reports"
    os.makedirs(report_path, exist_ok=True)
    report_file = os.path.join(
        report_path,
        f"detection_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"   ✅ 报告已保存: {report_file}")

    # ================================================================
    # 10. 纯文本模式演示（无时间信息）
    # ================================================================
    print("\n" + "=" * 60)
    print("  🧪 附加演示: 纯文本检测模式（无任何时间信息）")
    print("=" * 60)

    text_only_comments = generate_text_only_comments()
    print(f"\n📡 共 {len(text_only_comments)} 条评论（无 comment_time，无 order_time）")

    print("\n🔍 纯文本模型推理...")
    text_results = []
    for comment in text_only_comments:
        label, confidence = dm.predict(
            text=comment["text"],
            metadata={}  # 完全不传任何元数据
        )
        text_results.append({
            **comment, "label": label, "confidence": confidence,
            "model_confidence": confidence, "burst_score": 0.0,
        })

    text_burst_scores = burst_detector.detect(text_only_comments)
    print(f"   Burst检测: {sum(1 for s in text_burst_scores if s > 0)}/{len(text_only_comments)} 条聚集")

    for r in text_results:
        flag = "🚨" if r["label"] == "fake" else "✅"
        print(f"  {flag} [{r['label']:4s}] {r['confidence']:.1%} | {r['text'][:30]:30s} | @{r['username']:10s}")

    fake_in_text = sum(1 for r in text_results if r["label"] == "fake")
    print(f"\n  结果: {fake_in_text}/{len(text_results)} 条被判定为虚假（纯文本模式）")

    # ================================================================
    # 11. 告警推送
    # ================================================================
    if attack_info and attack_info["is_attack"]:
        print("\n🔔 [Reporter] 告警推送:")
        print(f"   → 商品 JD_PHONE_001 疑似遭受GEO投毒攻击")
        print(f"   → 攻击置信度: {attack_info['attack_confidence']}%")
        print(f"   → 建议人工复核")

    print("\n" + "=" * 60)
    print("  ✅ ReviewGuard 模拟演示完成!")
    print("  ✨ 改进亮点: 无需order_time | Burst聚类检测 | 纯文本降级")
    print("=" * 60)

    # 打印摘要
    print(f"\n📋 最终摘要:")
    print(f"  商品 JD_PHONE_001: {fake_count}/{len(results)} 条假评, "
          f"聚集{burst_hits}条, "
          f"攻击置信度 {attack_info['attack_confidence'] if attack_info else 0}%")
    print(f"  纯文本模式 (JD_TEXT_ONLY): {fake_in_text}/{len(text_only_comments)} 条假评")


if __name__ == "__main__":
    main()
