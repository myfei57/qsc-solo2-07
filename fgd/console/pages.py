"""控制台页面。

四个页面分别对应脱硫、脱硝、除尘与告警。页面本身只做展示：数据全部通过
``/api/status``、``/api/trend``、``/api/alarms`` 拉取，页面里的按钮走
``/api/start``、``/api/stop``、``/api/step``，不内嵌任何业务逻辑。
"""

from __future__ import annotations

from ..app import Platform

PAGE_TITLES: dict[str, str] = {
    "scrubber": "脱硫塔",
    "denox": "脱硝系统",
    "esp": "除尘电场",
    "alarms": "告警与审计",
}

_STYLE = """
body { font-family: "Microsoft YaHei", sans-serif; margin: 0; background: #10161d; color: #dfe7ef; }
header { padding: 14px 22px; background: #17222d; display: flex; align-items: center; gap: 18px; }
header h1 { font-size: 17px; margin: 0; font-weight: 600; }
nav a { color: #7fb2d9; margin-right: 14px; text-decoration: none; font-size: 14px; }
nav a.active { color: #ffd479; font-weight: 700; }
main { padding: 20px 22px; display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }
section { background: #17222d; border-radius: 8px; padding: 14px 16px; }
section h2 { font-size: 14px; margin: 0 0 10px; color: #9ec2e0; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
td { padding: 4px 6px; border-bottom: 1px solid #223142; }
td.k { color: #8fa6bb; width: 42%; }
button { background: #29618f; color: #fff; border: 0; border-radius: 4px; padding: 7px 14px; margin-right: 8px; cursor: pointer; }
button.warn { background: #8f5a29; }
pre { font-size: 12px; background: #0c1219; padding: 10px; border-radius: 6px; overflow-x: auto; max-height: 260px; }
"""

_SCRIPT = """
async function refresh() {
  const status = await (await fetch('/api/status')).json();
  const alarms = await (await fetch('/api/alarms')).json();
  const trend = await (await fetch('/api/trend?window_s=600')).json();
  document.getElementById('out-status').textContent = JSON.stringify(status, null, 2);
  document.getElementById('out-alarms').textContent =
    alarms.count + ' 条告警\\n' + JSON.stringify(alarms.alarms.slice(-8), null, 2);
  document.getElementById('out-trend').textContent = JSON.stringify(trend.series, null, 2);
}
async function act(path, payload) {
  const init = {method: 'POST', headers: {'Content-Type': 'application/json'}};
  if (payload) { init.body = JSON.stringify(payload); }
  const result = await (await fetch(path, init)).json();
  document.getElementById('out-action').textContent = JSON.stringify(result, null, 2);
  refresh();
}
window.addEventListener('DOMContentLoaded', () => {
  refresh();
  setInterval(refresh, 5000);
});
"""


def _nav(active: str, pages: tuple[str, ...]) -> str:
    items = []
    for page in pages:
        title = PAGE_TITLES.get(page, page)
        css = ' class="active"' if page == active else ""
        href = "/" if page == "scrubber" else f"/{page}"
        items.append(f'<a href="{href}"{css}>{title}</a>')
    return "<nav>" + "".join(items) + "</nav>"


def _sections(page: str) -> str:
    """每个页面给出各自关注的重点字段。"""

    focused = {
        "scrubber": "浆液循环、pH 闩锁、供浆与吸收塔效率",
        "denox": "供氨压力、喷氨率、阀门仲裁与催化剂效率",
        "esp": "电场电压、振打周期与出口含尘量",
        "alarms": "告警条目、审计摘要与落盘日志",
    }[page]
    return (
        "<section><h2>本页关注</h2>"
        f"<p style='font-size:13px;color:#9ec2e0'>{focused}</p>"
        "<button onclick=\"act('/api/start')\">启动顺序</button>"
        "<button class='warn' onclick=\"act('/api/stop')\">停运顺序</button>"
        "<button onclick=\"act('/api/step', {load: 0.85})\">推进周期</button>"
        "<pre id='out-action'>等待操作</pre></section>"
        "<section><h2>实时状态</h2><pre id='out-status'>加载中</pre></section>"
        "<section><h2>趋势窗口（10 分钟）</h2><pre id='out-trend'>加载中</pre></section>"
        "<section><h2>告警</h2><pre id='out-alarms'>加载中</pre></section>"
    )


def render_page(name: str, platform: Platform) -> str:
    """渲染一个控制台页面。"""

    if name not in PAGE_TITLES:
        raise KeyError(f"未知页面: {name}")
    pages = platform.config.pages
    title = PAGE_TITLES[name]
    return (
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>{platform.config.name} · {title}</title>"
        f"<style>{_STYLE}</style></head><body>"
        "<header><h1>"
        f"{platform.config.name} 控制台（{platform.config.project_id}）</h1>"
        f"{_nav(name, pages)}</header>"
        f"<main>{_sections(name)}</main>"
        f"<script>{_SCRIPT}</script></body></html>"
    )
