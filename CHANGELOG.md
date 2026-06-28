# Changelog

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
