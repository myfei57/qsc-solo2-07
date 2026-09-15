# FlueGasTreatment 烟气处理控制平台（zxy-117 / Python）

按 zxy-117 设计文档实现的燃煤电厂烟气脱硫脱硝与除尘控制平台。控制器按
「锅炉烟气 → 引风机 → 脱硝 → 除尘 → 脱硫 → 烟囱」的工艺链组织，组件之间
只通过显式状态查询与动作接口交互，所有顺序约束都是硬性的（不满足即拒绝并
写审计），不靠注释或文档约束约定。

## 目录结构

```
fgd/
  app.py           平台装配与控制循环（Platform / build_platform）
  cli.py           命令行：serve / demo / status / config / reset
  config.py        YAML 配置模型与校验
  clock.py         时钟抽象（系统时钟 / 可推进时钟）
  eventbus.py      进程内事件总线
  telemetry.py     趋势缓冲与补点
  ns/              烟气命名空间注册表、链路拓扑
  store/           原子 JSON 文档存储、分段 JSONL 日志
  audit/           审计记录与日志（总线持久化订阅者）
  fan/             引风机：状态机与落盘、升压／清烟、转速仲裁、两条负压回路
  stack/           锅炉烟气挡板、烟囱排放判定
  denox/           喷氨喷枪、NOx 回路、催化剂状态
  ammonia/         氨站供氨、喷氨阀仲裁、安全联锁
  esp/             电场升压、通烟、振打周期
  scrubber/        浆液循环、pH 闩锁、循环 pH 调节、停运切泵、分析仪标定
  absorber/        吸收塔气液接触、除雾器含尘折算
  lime/            浆液池、石灰浆供浆
  oxid/            氧化风机与投运
  sequence/        启动顺序与停运顺序（含顺序自查）
  console/         控制台 HTTP 服务、JSON 接口、四个页面
vendor/yaml/       离线 vendored 直接依赖（PyYAML 6.0.3 纯 Python 源码）
config/fgd.yaml    生效配置
tests/             单元与集成验证
tools/deadcode.py  交付树死代码／可达性门禁
```

## 硬顺序约束

| 约束 | 位置 |
| --- | --- |
| 引风机负压建立后才开锅炉烟气挡板 | `stack/opening.py` |
| 引风机负压落盘后才启动浆液循环 | `fan/state.py` + `scrubber/loop.py` |
| 供氨建立后才投脱硝喷枪 | `denox/inject.py` |
| 除尘电场升压后才通烟 | `esp/admit.py` |
| 氧化风机运行后才启动浆液循环 | `oxid/blower.py` + `scrubber/loop.py` |
| pH 回升到恢复值后闩锁自动复位、供浆恢复 | `scrubber/latch.py` + `lime/feed.py` |
| 排放判定始终使用最新标定基线 | `scrubber/offset.py` + `stack/control.py` |
| 烟气走完（清烟凭据）后才切浆液循环 | `fan/clear.py` + `scrubber/cut.py` |
| 两个负压回路／两个喷氨写入方共用执行器时按优先级仲裁 | `fan/speed.py` / `ammonia/valve.py` |

## 本地验证

```bash
python -m compileall -q fgd                 # 语法／字节码构建
python -m pytest -q                          # 单元与集成验证
python -m fgd demo --fast --cycles 16        # 全流程真实运行 + 页面探测
python tools/deadcode.py fgd                 # 死代码与模块可达性门禁
```

`demo` 会依次执行启动顺序、若干控制周期、并发仲裁探测、分析仪重新标定与
停运顺序，并对 `scrubber`、`denox`、`esp`、`alarms` 四个页面和 `/api/status`
发真实 HTTP 请求；任一步不通过都会以非零退出码结束。

## 控制台

```bash
python -m fgd --config config/fgd.yaml serve --host 127.0.0.1 --port 8080
```

页面：`/`（脱硫）、`/denox`、`/esp`、`/alarms`。

接口：`/api/status`、`/api/trend`、`/api/alarms`、`/api/audit`、`/api/journal`、
`/api/readiness`、`/api/start`、`/api/stop`、`/api/step`、`/api/calibrate`、
`/api/maintenance/catalyst`。

## 容器

```bash
docker build -t fgd-control-platform:1.0.0 .
docker run --rm -p 8080:8080 fgd-control-platform:1.0.0
```

镜像基于 `python:3.12-slim`，直接以 `PYTHONPATH=/app:/app/vendor` 运行，
依赖全部来自仓库内 `vendor/`，构建期与运行期都不需要联网安装。
