#!/usr/bin/env bash
# 构建烟气处理控制平台的多架构镜像。
#
# 用法: ./build_benzhi_docker.sh [image:tag]
# 默认镜像名: fgd-control-platform:1.0.0
#
# 说明:
#   * 镜像内 PIP_NO_INDEX=1，依赖全部来自仓库内 vendor/，构建不需要公网；
#   * 同时构建 linux/amd64 与 linux/arm64；
#   * 构建完成后跑一次容器内自检（启动控制台并探测就绪接口）。
set -euo pipefail

IMAGE="${1:-fgd-control-platform:1.0.0}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

docker buildx build \
  --platform "${PLATFORMS}" \
  --file benzhi.Dockerfile \
  --tag "${IMAGE}" \
  --load \
  .

echo "构建完成: ${IMAGE} (${PLATFORMS})"

if docker run --rm "${IMAGE}" python -c "import fgd, yaml; print('fgd', fgd.__version__, '| yaml', yaml.__version__)"; then
  echo "容器内依赖自检通过"
else
  echo "容器内依赖自检失败" >&2
  exit 1
fi
