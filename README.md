# ReviewGuard

一个中文电商虚假评论检测系统。四个 Agent 通过自写的消息总线串成流水线，自动完成评论采集、真假判断、攻击识别和报告生成。

没有用 LangChain 之类的 Agent 框架，消息总线、Agent 生命周期、工作流编排都是自己写的。

## 架构

```
Orchestrator (调度中心)
    │
    ├── Collector ──▶ Detector ──▶ Analyst ──▶ Reporter
    │    (采集)        (检测)       (分析)       (报告)
    │
    └── MessageBus (消息总线, 点对点 + 发布订阅)
```

四个 Agent 各干一件事，互相不知道对方存在，全部通过消息总线通信。好处是替换任何一个 Agent 都不用改其他代码。

### Collector

拿数据。目前从本地 7000+ 条中文评论数据集里随机采样。也留了京东爬虫的口子——换数据源只需要 `config.py` 里改一行 `DATA_SOURCE`。

### Detector

整个系统的核心。不是简单调个模型就完事，分三步走：

1. 用 `chinese-roberta-wwm-ext` 对每条评论做推理，得到一个基础假评概率。模型输入不只有文本，还拼接了 16 维手工特征：评论发布时间特征（凌晨发的？工作时间发的？）、用户名特征（是不是 `user1234` 这种机器号？）、文本统计特征（句长、标点密度、有没有具体细节）

2. 对所有评论做 Burst 时间聚类——如果一批评论在半小时内扎堆出现，大概率是批量刷的。这个算法只依赖 `comment_time`（评论发布时间），不依赖 `order_time`（下单时间）。京东上其实拿不到下单时间，所以这是一个务实的取舍。没时间信息的时候自动退到纯文本模式

3. 把模型分和聚集分融合（boost 模式），聚集分只加不减。聚集信号够强时能翻转模型原来的判断

模型是用 7000 条京东评论训练的，Focal Loss + 早停 + 阈值搜索。Test F1 0.928，假评召回率 97%。

### Analyst

把 Detector 的单条结果聚合成商品级别的判断：一个商品假评超过 5 条且比例过半，就判定为攻击事件。输出攻击画像——什么类型（刷单好评还是恶意差评）、评分分布、时间线。

### Reporter

出报告。JSON 一份、Markdown 一份。按假评比例分三级告警。如果配了 LLM 的话会自动写一段中文摘要。

## 消息总线

四个 Agent 之间不直接调用，全部走 MessageBus：

- 点对点发送：`bus.send(Message(receiver="detector", ...))`，指定接收者
- 发布订阅：`bus.publish("alert", msg)`，所有订阅了该 topic 的 Agent 都会收到
- 每个 Agent 有独立的 mailbox，有熔断机制（累计 5 次错误自动进入 error 状态）

## 跑起来

```bash
pip install -r requirements.txt

# 需要把 train_v2.csv 等数据集放到 data/ 目录下
# 模型文件 best_geo_poison_detector_v3.pt 和 meta_scaler.pkl 放根目录

python demo.py              # 模拟演示，最直观
python main.py once --products demo   # 从数据集随机采样检测
python main.py web                    # Web 面板 localhost:5000
```

## 项目结构

```
core/                   # Agent 框架
  message.py            #   消息定义
  bus.py                #   消息总线
  base_agent.py         #   Agent 基类（生命周期、熔断）
  orchestrator.py       #   调度中心
  burst_detector.py     #   Burst 时间聚类
  llm_client.py         #   LLM 客户端
  storage.py            #   SQLite 存储
agents/                 # 四个 Agent
  collector.py
  detector.py
  analyst.py
  reporter.py
models/
  detection_model.py    # RoBERTa + 16维特征
crawler/                # 数据源
  dataset_loader.py     #   本地数据集
  jd_crawler.py         #   京东 API
  selenium_crawler.py   #   浏览器爬虫（Chrome/Edge自适应）
web/
  app.py                # Flask Dashboard
config.py               # 全局配置
main.py                 # 主入口
demo.py                 # 完整演示
retrain_model.py        # 模型重训练
track_reviews.py        # 评论持续跟踪采集
```

## 技术栈

RoBERTa (transformers) + PyTorch, Flask, SQLite, Selenium

## License

MIT
