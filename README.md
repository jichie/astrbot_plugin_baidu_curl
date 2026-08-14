# 百度网盘 cURL 下载助手

> 发送百度网盘分享链接，自动转存、提取直链并整理文件

## 📦 安装方法

1. 确保已安装 [AstrBot](https://github.com/AstrBotDevs/AstrBot)
2. 将插件复制到 AstrBot 的插件目录（你也可以使用 AstrBot 的插件管理器安装，或下载本项目上传压缩包）
3. 重启 AstrBot 或使用热加载命令

---

## ✨ 功能

- 📦 **内置转存** - 直接用百度网盘 Cookie 转存分享文件到自己网盘，无需额外服务
- 🔗 **提取直链** - 通过 filemetas API 获取百度直链
- 🔧 **生成命令** - 生成完整的 cURL 下载命令
- 🔑 **自动刷新** - 从 OpenList 获取并刷新 OAuth token
- 📁 **文件整理** - 自动移动 sharelink 及日期目录到 `/来自Bot`
- ⏱️ **时间过滤** - 基于 `server_mtime` 只匹配本次转存的文件，防止提取旧文件
- 🔍 **智能扫描** - 自动发现 sharelink 和日期目录
- 🗑️ **过期清理** - 可配置自动删除 `/来自Bot` 中超过保留时长的文件
- 📂 **多文件选择** - 转存后如有多个文件，可让用户选择要提取直链的文件
- 🚀 **推送下载器** - 提取直链后推送到 Motrix（aria2 RPC），自动携带 UA + BDUSS Cookie 实现会员不限速下载（参考[网盘直链下载助手](https://greasyfork.org/zh-CN/scripts/512125)油猴脚本）
- 🔀 **三种模式** - 只提取直链 / 直接推送 / 显示直链后询问是否推送
- 🔒 **推送白名单** - 推送功能独立白名单，与提取直链权限分离

## 📋 前置依赖

| 服务 | 用途 | 说明 |
|------|------|------|
| **百度网盘 Cookie** | 转存文件 | 填入 `baidu_cookies` 配置项 |
| **OpenList/AList** | 获取 refresh_token 凭证 | [GitHub](https://github.com/AlistGo/alist) |

### 获取 Cookie

1. 浏览器登录 [pan.baidu.com](https://pan.baidu.com)
2. 按 F12 打开开发者工具
3. Network → 刷新页面 → 点击任意请求 → Headers → Cookie
4. 复制完整 Cookie 字符串，粘贴到 `baidu_cookies` 配置项
5. 插件会自动提取 BDUSS 和 STOKEN

## ⚙️ 配置项

安装完成后，在 AstrBot 管理面板中配置以下参数：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `baidu_cookies` | 百度网盘 Cookie | 粘贴完整 Cookie 字符串 |
| `save_dir` | 转存目标目录 | `/来自Bot` |
| `openlist_url` | OpenList 地址 | `http://192.168.1.207:7344` |
| `openlist_user` | OpenList 用户名 | `admin` |
| `openlist_pass` | OpenList 密码 | - |
| `openlist_pan_path` | OpenList 百度网盘挂载路径 | `/百度` |
| `show_curl_command` | 显示 cURL 命令 | `true` |
| `allow_sessions` | 允许的会话列表 | 留空则所有会话可用 |
| `file_retention_hours` | 文件保留时长（小时） | `24`（0 = 禁用自动清理） |
| `enable_file_selection` | 多文件时允许用户选择 | `true`（关闭则全部提取直链） |
| `push_mode` | 推送模式 | `link_only` / `push_only` / `link_and_push` |
| `push_whitelist` | 推送白名单（与 allow_sessions 独立） | 留空则禁止推送 |
| `motrix_rpc_url` | Motrix RPC 地址 | `http://127.0.0.1:16800/jsonrpc` |
| `motrix_rpc_token` | Motrix RPC 密钥 | 无则留空 |
| `motrix_download_dir` | Motrix 下载保存目录 | 留空用默认 |

## 🚀 使用方法

直接发送百度网盘分享链接：

```
https://pan.baidu.com/s/1xxxxxxx 提取码:xxxx
```

插件会自动完成以下流程：

1. 📦 用百度网盘 Cookie 转存文件
2. ⏳ 等待转存完成（5秒）
3. 🔍 从百度网盘扫描实际文件（时间过滤 + 智能目录发现）
4. 📂 如有多个文件且开启 `enable_file_selection`，列出编号列表等待用户选择
5. 🔑 刷新百度 access_token
6. 🔗 提取百度直链（仅对选中的文件）
7. 📁 移动 sharelink 和日期目录到 `/来自Bot`
8. 🧹 清理空目录
9. 🗑️ 清理 `/来自Bot` 中的过期文件（如已配置）
10. 🔧 输出 cURL 下载命令

### 📂 多文件选择

当分享链接包含多个文件且 `enable_file_selection` 开启时：

```
✅ 转存成功！
📁 /sharelink.xxxxx
📄 file1.flac, file2.mp3, file3.jpg

📂 共找到 3 个文件，请选择要提取直链的文件：

1. file1.flac（30.0MB）
2. file2.mp3（8.2MB）
3. file3.jpg（512.0KB）

💡 回复数字选择（多个用空格/逗号分隔，如 1 3）
💡 回复 0 或 all 选择全部
⏱️ 120秒内有效
```

- 回复 `1` 选择第一个文件
- 回复 `1 3` 或 `1,3` 选择多个文件
- 回复 `0`、`all`、`全部` 或 `所有` 选择全部文件
- 120 秒内未选择自动取消，重新发送链接即可
- 发送新的分享链接会自动取消上次选择

### 🚀 推送到下载器（Motrix）

`push_mode` 三种模式：

| 模式 | 行为 |
|------|------|
| `link_only` | 只提取直链（原行为） |
| `push_only` | 提取直链后自动推送到下载器，无需确认 |
| `link_and_push` | 显示直链后再询问是否推送（回复 `y` 推送 / `n` 跳过，60 秒内有效） |

**推送白名单**：`push_whitelist` 与 `allow_sessions` 相互独立。留空则所有会话均禁止推送（提取直链不受影响）；填写群号/用户 ID 后仅白名单内会话可触发推送。

**Motrix**：
1. 打开 Motrix → 设置 → RPC 服务 → 开启 RPC 服务（默认端口 16800）
2. 配置 `motrix_rpc_url`：`http://127.0.0.1:16800/jsonrpc`
3. 如设置了 RPC 密钥，填入 `motrix_rpc_token`

**会员不限速说明**：推送时自动携带 `User-Agent: pan.baidu.com`（与插件生成的 cURL 命令一致，固定值）和 `BDUSS` Cookie。Motrix/aria2 会用这些请求头实际请求百度直链，效果与在终端粘贴 cURL 命令相同，会员账号可不限速下载。

## 📝 输出示例

```
📦 转存中...
✅ 转存成功！
📁 /来自Bot
📄 文件名.flac
🔑 刷新百度 token...
🔗 获取百度直链...
📁 移动文件夹到 /来自Bot...
✅ 文件夹已移动到 /来自Bot
🔧 cURL 命令:

📄 文件名.flac:
curl -L -o "文件名.flac" -H "User-Agent:pan.baidu.com" "https://d.pcs.baidu.com/file/xxx..."
```

## 📝 cURL 命令格式

```bash
curl -L -o "文件名.mp4" \
  -H "User-Agent:pan.baidu.com" \
  "https://d.pcs.baidu.com/file/xxx?...&access_token=xxx"
```

- `-L`：跟随重定向
- `-o`：指定输出文件名
- `-H "User-Agent:pan.baidu.com"`：大文件（>20MB）必须用此 User-Agent

## 🔧 工作原理

1. **转存文件** - 用百度网盘 Cookie 调用 `share/verify` + `share/transfer` 接口转存
2. **扫描文件** - 使用百度网盘 API 扫描实际文件，通过 `server_mtime` 时间过滤避免匹配旧文件
3. **提取直链** - 通过 filemetas API 获取下载直链
4. **移动文件** - 通过 OpenList API 移动 sharelink 文件夹；通过百度 filemanager API 清理空目录
5. **过期清理** - 扫描 `/来自Bot`，删除超过 `file_retention_hours` 的文件

## ⚠️ 注意事项

- access_token 有效期约 8 小时，插件会自动刷新
- 百度直链有效期约 8 小时
- BDUSS/STOKEN Cookie 可能过期，需定期更新
- OpenList 需要挂载百度网盘
- 文件会自动整理到 `save_dir` 目录下
- 过期文件清理需设置 `file_retention_hours > 0`

## 🐛 常见问题

**Q: 转存成功但没找到文件？**
A: 插件会自动扫描百度网盘查找文件（包括 sharelink 和日期目录），并通过时间过滤确保只匹配本次转存的文件。

**Q: 直链提取失败？**
A: 检查 OpenList 配置是否正确，确保百度网盘已挂载。

**Q: 文件没有移动到指定目录？**
A: 检查 OpenList 的百度网盘挂载路径是否正确。

**Q: 转存失败提示 Cookie 过期？**
A: 重新获取百度网盘 Cookie 更新到 `baidu_cookies` 配置项。

**Q: 转存后提取了旧文件？**
A: v7.1+ 已通过 `server_mtime` 时间过滤解决，只匹配本次转存期间创建的文件。

## 📄 更新日志

### v8.4 (2026-08-14)
- 🚀 新增推送到下载器功能（Motrix / aria2 RPC），参考"网盘直链下载助手"油猴脚本
- 🔗 推送时自动携带 User-Agent（固定 `pan.baidu.com`，与 cURL 命令一致）和 BDUSS Cookie，下载器请求直链时与终端 cURL 效果一致，会员账号不限速下载
- 🔀 新增 `push_mode` 三模式：`link_only` 只提取直链 / `push_only` 直接推送 / `link_and_push` 显示直链后询问是否推送（回复 y/n，60 秒超时）
- 🔒 新增 `push_whitelist` 推送独立白名单，与 `allow_sessions` 分离
- 🖥️ 通过 aria2 JSON-RPC（`aria2.addUri`）推送，支持 RPC 密钥与保存目录
- 📄 直链输出优化：始终显示直链 URL，cURL 命令由 `show_curl_command` 控制

### v8.3 (2026-06-30)
- 🛠️ 重构 `_scan_files_sync`：提取通用递归扫描函数，嵌套从 7 层降至 3 层
- 🔧 提取 `_openlist_login_sync` 公共方法，消除重复登录代码
- 🗑️ 删除 `_move_files_sync` 死代码和 `has_actual_dir` 死分支（净删 ~130 行）
- ✨ 文件选择列表新增显示文件大小（自动格式化 B/KB/MB/GB/TB）
- ⚡ Token 缓存 1 小时，减少重复登录 OpenList
- ⚡ 时间过滤窗口从 60s 扩大到 300s
- 🐛 修复 `openlist_pan_path` 配置项未使用的问题（3 处硬编码 `/百度` 替换）
- 🐛 修复转存时重复列举文件的问题（透传 `share_info` 避免二次请求）
- 🧹 移除未使用的 `_refresh_token` / `_client_id` / `_client_secret` 字段
- 🔒 添加 `asyncio.Lock` 保护待选择状态的并发操作
- 🔒 所有 `except Exception` 增加堆栈信息
- 📦 添加 `curl_cffi` 依赖声明到 `requirements.txt`

### v8.2 (2026-06-29)
- ✨ 多文件选择改为先列出文件再转存选中的，未选中的不转存，节省网盘空间
- 📝 更新文档

### v8.1 (2026-06-28)
- 🧹 移除 baidu-autosave 依赖及相关代码，仅保留内置转存模式
- 🧹 移除 `transfer_mode`、`autosave_url`、`autosave_user`、`autosave_pass` 配置项
- ✨ `autosave_dir` 更名为 `save_dir`
- 📝 更新 README 文档

### v8.0 (2026-06-28)
- ✨ 新增内置转存模式：直接用百度网盘 Cookie 转存，无需部署额外服务
- ✨ 新增 `baidu_cookies` 配置项：支持粘贴完整 Cookie 字符串，插件自动提取 BDUSS 和 STOKEN
- ✨ 内置转存支持递归列出分享目录、分页获取子目录文件

### v7.3 (2026-06-28)
- ✨ 新增多文件选择功能：转存后如有多个文件，列出编号列表供用户选择要提取直链的文件
- ✨ 新增 `enable_file_selection` 配置项（默认开启，关闭后恢复全部提取直链的原行为）
- ✨ 支持多选（空格/逗号分隔）、回复 `0`/`all`/`全部`/`所有` 选择全部
- ✨ 120 秒超时自动取消，发送新链接可取消上次选择

### v7.2 (2026-06-07)
- ✨ 新增 `allow_sessions` 安全机制：空列表默认阻止所有会话，防止凭据泄露
- ✨ 新增插件 `logo.png`
- 🐛 修复搜索条件过窄导致 `share/init?surl=` 链接被忽略
- 🐛 修复多行字符串语法错误
- 🐛 修复 `show_curl_command` 配置未生效
- 🧹 移除未使用的 `bduss` 死代码

### v7.1 (2026-06-02)
- ✨ 新增 `server_mtime` 时间过滤，防止匹配网盘旧文件
- ✨ 新增智能目录扫描，自动发现日期目录和 sharelink
- ✨ 新增 `/来自Bot` 过期文件自动清理（通过 `file_retention_hours` 配置）
- ✨ 新增 `openlist_pan_path` 配置项
- 🐛 修复 `save_dir` 未更新导致路径显示错误
- 🐛 修复目录删除路径错误，支持逐级清理空目录

### v7.0 (2026-06-01)
- ✨ 新增自动移动 sharelink 文件夹功能
- ✨ 支持扫描根目录和 `/来自Bot` 目录的文件
- 🐛 修复 OpenList API 调用方式
- 🐛 修复 URL 编码问题

## 📜 许可证

MIT License
