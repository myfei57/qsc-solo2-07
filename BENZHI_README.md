基于 Python 实现的燃煤电厂烟气脱硫脱硝与除尘控制平台项目，一款面向湿法脱硫＋SCR 脱硝＋电袋除尘联合工艺的机组侧控制软件，覆盖引风机负压、锅炉烟气接入、喷氨脱硝、电场除尘、浆液循环与 pH 联锁、分析仪标定与烟囱排放判定的完整控制链。

## 业务定位

按「锅炉烟气 → 引风机 → 脱硝 → 除尘 → 脱硫 → 烟囱」的工艺链组织控制逻辑，
把现场最容易被接错的顺序关系写成硬约束：引风机建立负压并落盘后才允许开锅炉
烟气挡板，供氨建立后才允许投喷枪，电场升压后才允许通烟，氧化风机与已落盘
负压同时到位后才允许启动浆液循环，停运时必须等烟气走完才允许切循环泵。

## 运行方式

```bash
# 启动控制台（scrubber / denox / esp / alarms 四个页面 + JSON 接口）
python -m fgd --config config/fgd.yaml serve --host 0.0.0.0 --port 8080

# 本地跑通全流程：启动顺序 → 控制周期 → 并发仲裁探测 → 标定 → 停运顺序
python -m fgd demo --fast --cycles 16
```

## 环境

- Python 3.12（镜像 `python:3.12-slim`，容器内 `PYTHONPATH=/app:/app/vendor`）。
- 直接依赖 PyYAML 6.0.3 已放入 `vendor/yaml`（纯 Python 源码），镜像内
  `PIP_NO_INDEX=1`，构建与运行期都不访问公网。
- 存储根目录由 `platform.storage_root` 指定，默认 `var/state`，可由
  `--storage-root` 覆盖。
