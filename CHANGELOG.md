# Changelog

## v8.3.1 (2026-08-12)

### Bug 修复
- 🐛 修复缓存 token 过期导致扫描和直链获取失败的问题（errno=-6）：新增 `_invalidate_token()` 方法，scan 和 dlink 阶段若使用缓存 token 但结果为空时，自动失效缓存并重试

---

## v8.3.0 (2026-06-30)

### 重构与优化
- 重构 `_scan_files_sync`：提取 `_scan_recursive` + `_list_dir_api` 通用递归函数，三阶段扫描统一，降低嵌套深度（最大 7→3 层）
- 提取 `_openlist_login_sync` 公共方法，消除 `_move_single_dir_sync` / `_move_folder_sync` 中重复的 OpenList 登录代码
- 删除死代码 `_move_files_sync` 方法和 `has_actual_dir` 相关分支（~130 行）

### 功能改进
- 文件选择列表新增显示文件大小（自动格式化 B/KB/MB/GB/TB）
- `_refresh_access_token` 新增 1 小时缓存（`_TOKEN_CACHE_TTL`），减少重复登录 OpenList
- `server_mtime` 时间过滤窗口从 60s 扩大到 300s，避免因百度延迟漏匹配
- HTML JSON 解析增加字符串状态追踪，防止文件名中的花括号干扰

### 依赖
- `requirements.txt` 添加 `curl_cffi>=0.5.0`

### Bug 修复
- 修复 `openlist_pan_path` 配置项已定义但未使用的问题（3 处硬编码 `/百度` 替换）
- 修复转存时重复调用 `_list_share_files_sync` 的问题：透传 `share_info` 避免二次请求
- 移除未使用的 `_refresh_token` / `_client_id` / `_client_secret` 字段

### 安全
- `_pending_selections` 添加 `asyncio.Lock` 并发保护
- 所有 `except Exception` 增加堆栈信息：10 处 `logger.exception` + 10 处 `exc_info=True`

---

## v8.2.0 (2026-06-29)

### 优化
- 多文件选择改为先列出文件再转存选中的，不再转存全部文件
- 将转存拆分为 `_list_share_files`（列出文件）和 `_transfer_selected_files`（转存选中）两步
- 未选中的文件不会被转存，节省网盘空间
- 新增 `_do_transfer_and_dlinks` 方法统一处理转存+提取直链流程

---

## v8.1.0 (2026-06-28)

### 变更
- 移除 baidu-autosave 依赖及相关代码，仅保留内置转存模式
- 移除 `transfer_mode`、`autosave_url`、`autosave_user`、`autosave_pass` 配置项
- `autosave_dir` 更名为 `save_dir`
- 移除 `_autosave`、`_autosave_sync`、`_cleanup_autosave_task`、`_cleanup_sync` 方法

---

## v8.0.0 (2026-06-28)

### 新增
- 内置转存模式（`transfer_mode=builtin`）：直接用百度网盘 Cookie 转存，无需部署 baidu-autosave Docker 服务
- `baidu_cookies` 配置项：支持粘贴完整 Cookie 字符串，插件自动提取 BDUSS 和 STOKEN
- 内置转存支持递归列出分享目录、分页获取子目录文件
- 转存入口按 `transfer_mode` 分流：`builtin`（内置）或 `autosave`（baidu-autosave 服务）

### 修复
- 内置模式下自动跳过 baidu-autosave 任务清理，避免连接失败警告

---

## v7.3.0 (2026-06-28)

### 新增
- 多文件选择功能：转存后如有多个文件，列出编号列表供用户选择要提取直链的文件
- `enable_file_selection` 配置项（默认开启，关闭后恢复全部提取直链的原行为）
- 支持多选（空格/逗号分隔，如 `1 3`）、回复 `0`/`all`/`全部`/`所有` 选择全部
- 120 秒超时自动取消选择，发送新链接可取消上次选择
- 将直链提取逻辑抽取为 `_run_dlinks` 方法，供直接调用和选择流程复用

---

## v7.2.0 (2026-06-07)

### 新增
- `allow_sessions` 安全机制：空列表默认阻止所有会话，防止百度凭据泄露
- autosave 新消息格式兼容（`使用密码访问分享链接`）
- 插件 `logo.png`

### 修复
- 搜索条件过窄导致 `share/init?surl=` 链接被忽略
- 多行字符串语法错误
- `show_curl_command` 配置未生效
- 移除未使用的 `bduss` 死代码

---

## v7.1.0 (2026-06-02)

### 新增
- `server_mtime` 时间过滤，防止匹配网盘旧文件
- 智能目录扫描，自动发现 autosave 创建的日期目录和 sharelink
- `/来自Bot` 过期文件自动清理（`file_retention_hours` 配置）
- `openlist_pan_path` 配置项

### 修复
- `save_dir` 未更新导致路径显示错误
- `_scan_files_sync` 重复扫描代码
- 同步方法中 `aiohttp.ClientTimeout` 传参不兼容
- autosave 转存目录未被正确返回
- sharelink 文件夹误走 `_move_single_dir`
- 目录删除路径错误，逐级清理空目录

---

## v7.0.0 (2026-06-01)

### 新增
- 自动移动 sharelink 文件夹功能
- 自动清理 baidu-autosave 任务
- 支持扫描根目录和 `/来自Bot` 目录的文件

### 修复
- baidu-autosave 缓存导致的文件匹配问题
- OpenList API 调用方式
- URL 编码问题
