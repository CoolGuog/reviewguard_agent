"""
ReviewGuard Web Dashboard
Flask应用，提供监控界面和API
"""
import logging
from flask import Flask, render_template_string, jsonify, request

logger = logging.getLogger("ReviewGuard.Web")


def create_app(orchestrator):
    """创建Flask应用"""
    app = Flask(__name__)
    orch = orchestrator

    # ---- HTML Dashboard ----
    DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ReviewGuard Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }
        h1 { font-size: 24px; margin-bottom: 24px; color: #f8fafc; }
        h1 span { color: #38bdf8; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card { background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }
        .card h3 { font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
        .card .value { font-size: 32px; font-weight: 700; }
        .card .value.danger { color: #f87171; }
        .card .value.success { color: #4ade80; }
        .card .value.info { color: #38bdf8; }
        .card .value.warn { color: #fbbf24; }
        .section { margin-bottom: 24px; }
        .section h2 { font-size: 18px; margin-bottom: 12px; color: #f1f5f9; }
        table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; }
        th, td { padding: 10px 16px; text-align: left; border-bottom: 1px solid #334155; font-size: 13px; }
        th { background: #0f172a; color: #94a3b8; font-weight: 600; }
        tr:hover { background: #334155; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }
        .badge-fake { background: #7f1d1d; color: #fca5a5; }
        .badge-real { background: #14532d; color: #86efac; }
        .badge-critical { background: #7f1d1d; color: #fca5a5; }
        .badge-warning { background: #713f12; color: #fde047; }
        .badge-info { background: #1e3a5f; color: #93c5fd; }
        .actions { margin-bottom: 24px; display: flex; gap: 12px; }
        .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; font-weight: 600; }
        .btn-primary { background: #2563eb; color: white; }
        .btn-primary:hover { background: #1d4ed8; }
        .btn-danger { background: #dc2626; color: white; }
        .btn-danger:hover { background: #b91c1c; }
        .agent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
        .agent-card { background: #1e293b; border-radius: 8px; padding: 16px; border: 1px solid #334155; }
        .agent-card .name { font-weight: 700; color: #38bdf8; margin-bottom: 4px; }
        .agent-card .state { font-size: 12px; color: #94a3b8; }
        .empty { color: #64748b; text-align: center; padding: 40px; }
    </style>
</head>
<body>
    <h1>🛡️ Review<span>Guard</span> Dashboard</h1>

    <div class="actions">
        <button class="btn btn-primary" onclick="startPipeline()">▶ 启动检测流水线</button>
        <button class="btn btn-danger" onclick="clearData()">🗑 清空数据</button>
    </div>

    <div class="grid" id="stats-cards">
        <div class="card">
            <h3>总评论数</h3>
            <div class="value info" id="stat-total">-</div>
        </div>
        <div class="card">
            <h3>虚假评论</h3>
            <div class="value danger" id="stat-fake">-</div>
        </div>
        <div class="card">
            <h3>真实评论</h3>
            <div class="value success" id="stat-real">-</div>
        </div>
        <div class="card">
            <h3>攻击事件</h3>
            <div class="value warn" id="stat-events">-</div>
        </div>
    </div>

    <div class="section">
        <h2>Agent 状态</h2>
        <div class="agent-grid" id="agent-status"></div>
    </div>

    <div class="section">
        <h2>最近攻击事件</h2>
        <table id="events-table">
            <thead>
                <tr><th>事件ID</th><th>商品</th><th>假评数</th><th>假评比例</th><th>攻击类型</th><th>严重程度</th><th>检测时间</th></tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>

    <div class="section">
        <h2>最近检测结果</h2>
        <table id="reviews-table">
            <thead>
                <tr><th>商品</th><th>评论</th><th>评分</th><th>标签</th><th>置信度</th><th>状态</th></tr>
            </thead>
            <tbody></tbody>
        </table>
    </div>

    <script>
        function startPipeline() {
            const pid = prompt('输入商品ID（多个用逗号分隔）:', '');
            if (!pid) return;
            const products = pid.split(',').map(s => s.trim());
            fetch('/api/pipeline', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({product_ids: products})
            }).then(r => r.json()).then(d => {
                alert(d.message);
                refresh();
            });
        }

        function clearData() {
            if (!confirm('确认清空所有数据?')) return;
            fetch('/api/data', {method: 'DELETE'}).then(() => refresh());
        }

        function refresh() {
            fetch('/api/stats').then(r => r.json()).then(d => {
                document.getElementById('stat-total').textContent = d.total_reviews;
                document.getElementById('stat-fake').textContent = d.fake_reviews;
                document.getElementById('stat-real').textContent = d.real_reviews;
                document.getElementById('stat-events').textContent = d.attack_events;
            });

            fetch('/api/status').then(r => r.json()).then(d => {
                const el = document.getElementById('agent-status');
                el.innerHTML = Object.entries(d.agents).map(([name, info]) =>
                    `<div class="agent-card"><div class="name">${name}</div><div class="state">状态: ${info.state} | 消息: ${info.mailbox_size} | 错误: ${info.error_count}</div></div>`
                ).join('');
            });

            fetch('/api/events').then(r => r.json()).then(d => {
                const tbody = document.querySelector('#events-table tbody');
                if (!d.length) { tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无攻击事件</td></tr>'; return; }
                tbody.innerHTML = d.map(e => `<tr>
                    <td>${e.event_id}</td><td>${e.product_id}</td>
                    <td>${e.fake_count}</td><td>${(e.fake_ratio*100).toFixed(1)}%</td>
                    <td>${e.attack_type}</td>
                    <td><span class="badge badge-${e.severity}">${e.severity}</span></td>
                    <td>${e.detected_at}</td>
                </tr>`).join('');
            });

            fetch('/api/reviews?limit=20').then(r => r.json()).then(d => {
                const tbody = document.querySelector('#reviews-table tbody');
                if (!d.length) { tbody.innerHTML = '<tr><td colspan="6" class="empty">暂无检测数据</td></tr>'; return; }
                tbody.innerHTML = d.map(r => `<tr>
                    <td>${r.product_id}</td><td>${r.text.slice(0,40)}...</td>
                    <td>${r.score}</td>
                    <td><span class="badge badge-${r.label}">${r.label}</span></td>
                    <td>${(r.confidence*100).toFixed(1)}%</td>
                    <td>${r.review_status}</td>
                </tr>`).join('');
            });
        }

        refresh();
        setInterval(refresh, 10000);
    </script>
</body>
</html>
    """

    # ---- API 路由 ----
    @app.route("/")
    def index():
        return render_template_string(DASHBOARD_HTML)

    @app.route("/api/stats")
    def api_stats():
        if hasattr(orch, "storage"):
            return jsonify(orch.storage.get_stats())
        return jsonify({"total_reviews": 0, "fake_reviews": 0, "real_reviews": 0, "attack_events": 0})

    @app.route("/api/status")
    def api_status():
        return jsonify(orch.get_status())

    @app.route("/api/events")
    def api_events():
        if hasattr(orch, "storage"):
            return jsonify(orch.storage.get_attack_events(limit=20))
        return jsonify([])

    @app.route("/api/reviews")
    def api_reviews():
        limit = request.args.get("limit", 20, type=int)
        if hasattr(orch, "storage"):
            return jsonify(orch.storage.get_recent_reviews(limit=limit))
        return jsonify([])

    @app.route("/api/pipeline", methods=["POST"])
    def api_start_pipeline():
        data = request.get_json() or {}
        product_ids = data.get("product_ids", [])
        try:
            orch.start_pipeline({"product_ids": product_ids})
            return jsonify({"message": f"流水线已启动, 商品: {product_ids}", "status": "ok"})
        except Exception as e:
            return jsonify({"message": f"启动失败: {e}", "status": "error"}), 500

    @app.route("/api/data", methods=["DELETE"])
    def api_clear_data():
        if hasattr(orch, "storage"):
            conn = orch.storage._conn
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reviews")
            cursor.execute("DELETE FROM attack_events")
            cursor.execute("DELETE FROM detection_stats")
            conn.commit()
        return jsonify({"status": "ok"})

    return app
