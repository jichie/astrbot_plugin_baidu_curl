# 百度网盘 cURL 下载助手

> 发送百度网盘分享链接，自动转存、提取直链并整理文件

## 📦 安装方法

1. 确保已安装 [AstrBot](https://github.com/AstrBotDevs/AstrBot)
2. 将插件复制到 AstrBot 的插件目录（你也可以使用 AstrBot 的插件管理器安装，或下载本项目上传压缩包）
3. 重启 AstrBot 或使用热加载命令

---

## ✨ 功能

- 📦 **自动转存** - 调用 baidu-autosave 转存分享文件到自己网盘
- 🔗 **提取直链** - 通过 filemetas API 获取百度直链
- 🔧 **生成命令** - 生成完整的 cURL 下载命令
- 🔑 **自动刷新** - 从 OpenList 获取并刷新 OAuth token
- 📁 **文件整理** - 自动将 sharelink 文件夹移动到指定目录
- 🧹 **任务清理** - 完成后自动清理 baidu-autosave 任务
- 🔄 **智能匹配** - 支持转存到根目录或 `/来自Bot` 目录的文件

## 📋 前置依赖

| 服务 | 用途 | 部署方式 |
|------|------|---------|
| **baidu-autosave** | 百度网盘自动转存 | Docker 部署，[GitHub](https://github.com/kokojacket/baidu-autosave) |
| **OpenList/AList** | 获取 refresh_token 凭证 | [GitHub](https://github.com/AlistGo/alist) |

## ⚙️ 配置项

安装完成后，在 AstrBot 管理面板中配置以下参数：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `autosave_url` | baidu-autosave 地址 | `http://192.168.1.207:5000` |
| `autosave_user` | baidu-autosave 用户名 | `admin` |
| `autosave_pass` | baidu-autosave 密码 | - |
| `autosave_dir` | 转存目标目录 | `/来自Bot` |
| `openlist_url` | OpenList 地址 | `http://192.168.1.207:7344` |
| `openlist_user` | OpenList 用户名 | `admin` |
| `openlist_pass` | OpenList 密码 | - |
| `show_curl_command` | 显示 cURL 命令 | `true` |
| `allow_sessions` | 允许的会话列表 | 留空则所有会话可用 |

## 🚀 使用方法

直接发送百度网盘分享链接：

```
https://pan.baidu.com/s/1xxxxxxx 提取码:xxxx
```

插件会自动完成以下流程：

1. 📦 调用 baidu-autosave 转存文件
2. ⏳ 等待转存完成（5秒）
3. 🔍 从百度网盘扫描实际文件
4. 🔑 刷新百度 access_token
5. 🔗 提取百度直链
6. 📁 移动 sharelink 文件夹到 `/来自Bot`
7. 🧹 清理 baidu-autosave 任务
8. 🔧 输出 cURL 下载命令

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

1. **转存文件** - 通过 baidu-autosave API 添加并执行转存任务
2. **扫描文件** - 使用百度网盘 API 扫描实际文件（不依赖缓存）
3. **提取直链** - 通过 filemetas API 获取下载直链
4. **移动文件** - 通过 OpenList API 移动 sharelink 文件夹
5. **清理任务** - 删除 baidu-autosave 中的任务记录

## ⚠️ 注意事项

- access_token 有效期约 8 小时，插件会自动刷新
- 百度直链有效期约 8 小时
- baidu-autosave 需要配置好百度网盘 cookies
- OpenList 需要挂载百度网盘
- 文件会自动整理到 `autosave_dir` 目录下

## 🐛 常见问题

**Q: 转存成功但没找到文件？**
A: 插件会自动扫描百度网盘查找文件，支持根目录和子目录。

**Q: 直链提取失败？**
A: 检查 OpenList 配置是否正确，确保百度网盘已挂载。

**Q: 文件没有移动到指定目录？**
A: 检查 OpenList 的百度网盘挂载路径是否正确。

## 📄 更新日志

### v7 (2026-06-01)
- ✨ 新增自动移动 sharelink 文件夹功能
- ✨ 新增自动清理 baidu-autosave 任务
- ✨ 支持扫描根目录和 `/来自Bot` 目录的文件
- 🐛 修复 baidu-autosave 缓存导致的文件匹配问题
- 🐛 修复 OpenList API 调用方式
- 🐛 修复 URL 编码问题
- 📝 更新 README 文档

## 📜 许可证

MIT License
