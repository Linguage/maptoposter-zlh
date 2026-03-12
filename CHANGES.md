# MapToposter 代码修改记录

## 修改日期
2025-01-19

---

## 修改概述

本次修改主要解决了三个问题：API超时、城市坐标定位偏移、画幅比例不支持自定义。

---

## 1. 修复 Nominatim API 超时问题

### 问题描述
原代码使用 geopy 的 Nominatim 地理编码服务时，默认超时时间仅1秒，在网络环境不佳时容易连接超时。

### 修改位置
文件: `create_map_poster.py` 第207行

### 修改内容
```python
# 修改前
location = geolocator.geocode(f"{city}, {country}")

# 修改后
location = geolocator.geocode(f"{city}, {country}", timeout=10)
```

---

## 2. 修复城市坐标定位偏移问题

### 问题描述
对于北京、成都等大城市，Nominatim 返回的是省级行政区（state）的几何中心，而非真正的市中心。

**示例**:
- 北京: 返回 40.19°N, 116.41°E（省级中心，偏北）
- 实际应为: 39.91°N, 116.39°E（市中心/东城区）

### 修改位置
文件: `create_map_poster.py` 第196-256行 (`get_coordinates` 函数)

### 修改内容
1. 获取多个候选结果（最多5个）
2. 优先选择 `city`、`suburb`、`town` 或 `village` 级别的结果
3. 只有在找不到城市级别结果时才降级到 `state` 级别

### 新增功能
- **地标搜索**: 通过 `--landmark` 参数指定具体地标
- **自定义坐标**: 通过 `--coords` 参数直接输入经纬度

---

## 3. 新增自定义画幅比例功能

### 问题描述
原代码只支持固定的竖屏（12×16）和横屏（16×12），地图在不同比例下会产生拉伸变形。

### 修改位置
文件: `create_map_poster.py` 第259-330行 (`create_poster` 函数)

### 修改内容
1. 移除固定的 `--orientation` 参数
2. 新增 `--ratio` 参数，支持任意宽高比（如 16:9, 9:16, 4:3）
3. 根据画幅比例自动调整地图覆盖范围（水平和垂直距离分别计算）
4. 设置 `ax.set_aspect('equal')` 确保地图不变形

### 比例计算逻辑
```python
# 解析比例 (如 "16:9")
width, height = map(float, ratio.split(':'))
scale = 12 / max(width, height)
figsize = (width * scale, height * scale)

# 根据比例调整覆盖范围
if aspect_ratio > 1:  # 横屏
    dist_horiz = dist
    dist_vert = dist / aspect_ratio
else:  # 竖屏或正方形
    dist_horiz = dist * aspect_ratio
    dist_vert = dist
```

---

## 4. 新增命令行参数

| 参数 | 短参数 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| `--ratio` | `-r` | string | `1:1` | 画幅比例（宽:高），如 16:9, 9:16, 4:3 |
| `--landmark` | `-l` | string | 无 | 地标名称，用于精确定位 |
| `--coords` | 无 | string | 无 | 自定义坐标，格式 "lat,lon" |

---

## 5. 依赖变更

新增 Python 模块导入:
```python
import math  # 用于比例计算中的三角函数
```

---

## 使用示例

### 基础用法
```bash
python3 create_map_poster.py -c "Beijing" -C "China" -t noir
```

### 自定义画幅比例
```bash
# 16:9 横屏
python3 create_map_poster.py -c "Chengdu" -C "China" -t midnight_blue -r 16:9

# 9:16 竖屏（适合手机壁纸）
python3 create_map_poster.py -c "Shanghai" -C "China" -t japanese_ink -r 9:16

# 4:3 横屏
python3 create_map_poster.py -c "Guangzhou" -C "China" -t sunset -r 4:3
```

### 地标定位
```bash
# 以天安门广场为中心
python3 create_map_poster.py -c "Beijing" -C "China" -t noir -l "Tiananmen Square"

# 以故宫为中心
python3 create_map_poster.py -c "Beijing" -C "China" -t midnight_blue -l "Forbidden City"
```

### 自定义坐标
```bash
# 直接指定经纬度
python3 create_map_poster.py -c "Beijing" -C "China" -t noir --coords "39.91,116.40"
```

---

## 修改效果对比

### 北京坐标修正
| 项目 | 修改前 | 修改后 |
|------|--------|--------|
| 返回地址 | 北京市, 中国 | 北京市, 东城区, 北京市, 100010, 中国 |
| 坐标 | 40.19°N, 116.41°E | 39.91°N, 116.39°E |
| 类型 | state（省级） | city（城市级） |

### 成都 16:9 地图
| 项目 | 数值 |
|------|------|
| 画布尺寸 | 12×7 |
| 宽高比 | 1.78 (≈16:9) |
| 水平覆盖 | 29000m |
| 垂直覆盖 | 16312m |

---

## 已知问题

无

---

## 后续优化建议

1. 添加更多预设比例选项（如 21:9 超宽屏）
2. 支持自定义输出 DPI
3. 添加地图边距控制
4. 支持批量生成多个城市
