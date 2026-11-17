# 发布利率债看板

GitHub Pages 托管网页和数据文件。GitHub Actions 读取给定的行情快照并构建网页。默认发布模拟行情，包含三个示例交易日。

## 首次发布

1. 将项目代码推送至 GitHub 仓库的默认分支。`.gitignore` 排除了本地数据库、原始响应、运行环境和验证截图。
2. 在仓库打开 **Settings → Pages**，将 **Source** 设为 **GitHub Actions**。
3. 打开 **Actions → 更新并发布债券看板 → Run workflow**。选择默认分支并运行。成功后，Pages 部署任务会显示站点地址。

分享站点地址即可让其他人打开看板。对其他网页提供三个 JSON 路径，均相对于站点根地址。

| 路径 | 内容 |
| --- | --- |
| `data/latest.json` | 最新日期的个券行情和来源时间 |
| `data/index.json` | 可查日期及数据模式 |
| `data/YYYY-MM-DD.json` | 指定日期的快照 |

`mode: "demo"` 标明模拟数据，`mode: "live"` 标明配置的数据源。债券代码按字符串传输。CSV 导入 Excel 时请将代码列指定为文本，以保留前导零。

## 接入行情并启用更新

中国货币网要求自动获取和对外转载取得书面授权。若使用该来源，请先确认授权覆盖获取方式和分发范围，或使用另一家允许网页分发的行情。参见[中国货币网法律声明](https://www.chinamoney.com.cn/chinese/legaldeclaration/)和[信息商数据服务](https://www.chinamoney.com.cn/chinese/xxssjfw/)。

在 **Settings → Secrets and variables → Actions** 配置发布权限覆盖的行情源。

| 位置 | 名称 | 用途 |
| --- | --- | --- |
| Repository secrets | `BOND_FEED_URL` | 返回行情 JSON 的最终 HTTPS 地址 |
| Repository secrets | `BOND_FEED_TOKEN` | 可选，源服务要求 Bearer Token 时填写 |
| Repository variables | `BOND_PUBLISH_MODE` | 设置为 `live` 启用行情发布 |

数据格式见 [demo-feed.json](web/demo-feed.json)。每个来源日期使用 `YYYY-MM-DD`，来源时间包含时区并与北京时间日期一致。价格用元，收益率用百分数，涨跌用 BP，交易量用亿，缺失值用 `null`。如果要展示历史，数据源需一并提供所需的日快照，最多 366 日。构建校验失败时留用上次已发布的网页。

设置完成后用 **Run workflow** 验证一次。之后 GitHub Actions 每天北京时间 **20:37** 触发。页面中的“刷新行情”读取最近已发布的快照，仓库里的 **Run workflow** 重新取数并发布。

## 本地预览

在项目目录执行以下命令。使用 Python 3.11 或以上版本，输出到一个新的空目录。

```powershell
python -m scripts.build_pages
python -m http.server 8852 --bind 127.0.0.1 --directory _site
```

打开 `http://127.0.0.1:8852/`。本地 Streamlit 工具可以继续通过 `Start.cmd` 启动。
