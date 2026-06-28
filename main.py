"""
百度网盘 cURL 下载助手 v8.0
内置转存（BDUSS Cookie）+ refresh_token 刷新 + filemetas API 获取直链
支持多文件时用户选择要提取直链的文件
"""

from __future__ import annotations

import asyncio
import time
import json
import re
import random
from typing import Optional

import aiohttp
import urllib.parse
from curl_cffi import requests as cffi_requests

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.platform.astr_message_event import AstrMessageEvent


def _parse(text: str) -> dict:
    r = {"surl": "", "pwd": ""}
    for p in [r"pan\.baidu\.com/s/([a-zA-Z0-9_-]+)", r"surl=([a-zA-Z0-9_-]+)"]:
        m = re.search(p, text)
        if m:
            s = m.group(1)
            if len(s) > 1 and s[0] == "1":
                s = s[1:]
            r["surl"] = s
            break
    for p in [
        r"提取码[：:\s]*([a-zA-Z0-9]{4})",
        r"密码[：:\s]*([a-zA-Z0-9]{4})",
        r"pwd[=：:\s]*([a-zA-Z0-9]{4})",
        r"[:\s]([a-zA-Z0-9]{4})\s*$",
    ]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            r["pwd"] = m.group(1)
            break
    return r


class BaiduCurlPlugin(Star):
    _RE = re.compile(r"https?://pan\.baidu\.com/s/[a-zA-Z0-9_-]+")
    _SELECTION_TIMEOUT = 120  # 文件选择超时时间（秒）

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        cfg = dict(config or {})
        self.allow_sessions: list = cfg.get("allow_sessions", [])
        # 转存目录
        self.save_dir: str = cfg.get("save_dir", "/来自Bot")
        # OpenList (用于获取 refresh_token 等凭证)
        self.openlist_url: str = cfg.get("openlist_url", "").rstrip("/")
        self.openlist_user: str = cfg.get("openlist_user", "")
        self.openlist_pass: str = cfg.get("openlist_pass", "")
        # 显示设置
        self.show_curl_command: bool = cfg.get("show_curl_command", True)
        # 文件清理
        self.file_retention_hours: int = int(cfg.get("file_retention_hours", 0) or 0)
        # 多文件选择
        self.enable_file_selection: bool = cfg.get("enable_file_selection", True)
        # 百度网盘 Cookie
        self.baidu_cookies: str = cfg.get("baidu_cookies", "")
        # 缓存
        self._access_token: str = ""
        self._token_expire: float = 0  # token 过期时间戳
        self._refresh_token: str = ""
        self._client_id: str = ""
        self._client_secret: str = ""
        # 待选择的文件状态: session_id -> {files, save_dir, surl, has_actual_dir, timestamp}
        self._pending_selections: dict = {}

    async def terminate(self):
        pass

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str
        sid = event.session_id

        # ---- 检查是否有待处理的文件选择 ----
        if sid in self._pending_selections:
            state = self._pending_selections[sid]
            # 超时检查
            if time.time() - state["timestamp"] > self._SELECTION_TIMEOUT:
                del self._pending_selections[sid]
                yield event.plain_result("⏰ 文件选择已超时，请重新发送链接")
                return

            selection_text = text.strip()
            # 如果是数字选择、all、0、全部、所有
            if re.match(r"^[\d,\s]+$", selection_text) or selection_text.lower() in (
                "all",
                "0",
                "全部",
                "所有",
            ):
                async for msg in self._handle_selection(event, selection_text):
                    yield msg
                return

            # 如果是新的分享链接，取消上次选择，继续处理新链接
            if self._RE.search(text):
                del self._pending_selections[sid]
                yield event.plain_result("ℹ️ 已取消上次的文件选择，开始处理新链接...")
                # 继续往下处理新链接（不 return）
            else:
                # 非选择消息，忽略但保持等待状态
                yield event.plain_result(
                    "⏳ 请回复数字选择文件（1-"
                    + str(len(state["share_files"]))
                    + "），多个用空格/逗号分隔\n回复 0 或 all 选择全部\n回复分享链接可取消选择"
                )
                return

        # 如果是影视转存命令，跳过（由 media_save 插件处理）
        if re.match(
            r"^(电影|电视剧|动漫|综艺|纪录片|movie|tv|anime|动画|转存)", text.strip()
        ):
            return
        if not self._RE.search(text):
            return
        if self.allow_sessions:
            if event.session_id not in self.allow_sessions:
                return
        else:
            yield event.plain_result(
                "⚠️ 插件未配置允许的会话，已自动阻止。\n"
                "请在 设置 → allow_sessions 中添加允许的 session_id。\n"
                "⚠️ 严禁在所有会话下开放此插件，否则可能导致百度凭据泄露！"
            )
            return
        if not self.baidu_cookies:
            yield event.plain_result(
                "⚠️ 未配置百度网盘 Cookie\n"
                "请在设置中填入 baidu_cookies（完整 Cookie 字符串）"
            )
            return

        parsed = _parse(text)
        if not parsed["surl"]:
            yield event.plain_result("❌ 无法解析链接")
            return

        surl, pwd = parsed["surl"], parsed["pwd"]
        yield event.plain_result("🔍 surl=" + surl + (" 提取码=" + pwd if pwd else ""))

        async for msg in self._run(event, surl, pwd):
            yield msg

    async def _run(self, ev: AstrMessageEvent, surl: str, pwd: str):
        # 1. 列出分享文件（不转存）
        yield ev.plain_result("📋 获取分享文件列表...")
        lr = await self._list_share_files(surl, pwd)
        if not lr.get("success"):
            yield ev.plain_result("❌ " + lr.get("error", "未知"))
            return

        share_files = lr["files"]  # [{name, fs_id, path}]

        # 2. 多文件选择
        if self.enable_file_selection and len(share_files) > 1:
            lines = []
            for i, f in enumerate(share_files, 1):
                lines.append(f"{i}. {f['name']}")

            self._pending_selections[ev.session_id] = {
                "share_files": share_files,
                "surl": surl,
                "pwd": pwd,
                "timestamp": time.time(),
            }

            yield ev.plain_result(
                f"📂 共 {len(share_files)} 个文件，请选择要转存并提取直链的文件：\n\n"
                + "\n".join(lines)
                + "\n\n💡 回复数字选择（多个用空格/逗号分隔，如 1 3）\n"
                + "💡 回复 0 或 all 选择全部\n"
                + f"⏱️ {self._SELECTION_TIMEOUT}秒内有效"
            )
            return  # 等待用户选择

        # 单文件或未启用选择：直接转存全部
        selected_indices = list(range(len(share_files)))
        async for msg in self._do_transfer_and_dlinks(ev, surl, pwd, share_files, selected_indices):
            yield msg

    async def _handle_selection(self, ev: AstrMessageEvent, selection_text: str):
        """处理用户的多文件选择"""
        sid = ev.session_id
        state = self._pending_selections.get(sid)
        if not state:
            yield ev.plain_result("❌ 没有待选择的文件，请重新发送链接")
            return

        # 超时检查
        if time.time() - state["timestamp"] > self._SELECTION_TIMEOUT:
            del self._pending_selections[sid]
            yield ev.plain_result("⏰ 文件选择已超时，请重新发送链接")
            return

        share_files = state["share_files"]
        surl = state["surl"]
        pwd = state["pwd"]

        # 解析选择
        text_lower = selection_text.strip().lower()
        indices = []

        if text_lower in ("0", "all", "全部", "所有"):
            indices = list(range(len(share_files)))
        else:
            nums = re.split(r"[,\s]+", text_lower)
            for n in nums:
                n = n.strip()
                if n.isdigit():
                    idx = int(n)
                    if 1 <= idx <= len(share_files):
                        indices.append(idx - 1)

            if not indices:
                yield ev.plain_result(
                    f"❌ 无效的选择，请回复数字（1-{len(share_files)}）\n"
                    + "多个用空格/逗号分隔，回复 0 或 all 选择全部"
                )
                return

            indices = sorted(set(indices))

        # 清理待选择状态
        del self._pending_selections[sid]

        # 显示已选文件
        selected_names = [share_files[i]["name"] for i in indices]
        yield ev.plain_result(
            f"📋 已选择 {len(indices)} 个文件：\n" + "\n".join(selected_names)
        )

        # 转存选中的文件并提取直链
        async for msg in self._do_transfer_and_dlinks(ev, surl, pwd, share_files, indices):
            yield msg

    async def _do_transfer_and_dlinks(
        self,
        ev: AstrMessageEvent,
        surl: str,
        pwd: str,
        share_files: list,
        selected_indices: list,
    ):
        """转存选中的文件，然后提取直链、移动、清理"""
        # 1. 转存
        yield ev.plain_result("📦 转存中...")
        tr = await self._transfer_selected_files(surl, pwd, selected_indices)
        if not tr.get("success"):
            yield ev.plain_result("❌ 转存失败: " + tr.get("error", "未知"))
            return

        save_dir = tr.get("save_dir", self.save_dir)
        transfer_files = tr.get("files", [])
        existed = tr.get("existed", False)

        yield ev.plain_result(
            "✅ 转存成功！\n📁 " + save_dir + "\n📄 " + ", ".join(transfer_files)
        )

        # 2. 等待转存完成
        await asyncio.sleep(3)

        # 3. 用文件名在百度网盘里匹配（扫描确认）
        cutoff_time = 0 if existed else (int(time.time()) - 60)
        if existed:
            logger.info("[scan] 文件已存在，跳过时间过滤")
        logger.info(f"[scan] 要匹配的文件: {transfer_files}, 已存在: {existed}")

        files = []
        has_actual_dir = False

        token_ok = await self._refresh_access_token()
        if token_ok and self._access_token:
            scan_files = transfer_files if transfer_files else None
            at = self._access_token
            loop = asyncio.get_running_loop()
            files, final_dir = await loop.run_in_executor(
                None,
                self._scan_files_sync,
                at,
                scan_files,
                self.save_dir,
                save_dir,
                [],
                has_actual_dir,
                cutoff_time,
            )
            if final_dir:
                save_dir = final_dir

        if not files:
            # 扫描不到就用转存返回的文件名兜底
            files = [f"{save_dir}/{fn}" for fn in transfer_files]

        # 4. 提取直链
        async for msg in self._run_dlinks(ev, files, save_dir, surl, has_actual_dir):
            yield msg

    async def _run_dlinks(
        self,
        ev: AstrMessageEvent,
        files: list,
        save_dir: str,
        surl: str,
        has_actual_dir: bool,
    ):
        """提取直链、移动文件夹、清理（从 _run 分离，可被选择流程复用）"""
        search_dirs = set()
        search_dirs.add(save_dir)
        for f in files:
            parts = f.strip("/").split("/")
            if len(parts) > 1:
                search_dirs.add("/" + parts[0])
        logger.info(f"[dlink] 搜索目录: {search_dirs}")

        if self.openlist_url and self.openlist_user:
            yield ev.plain_result("🔑 刷新百度 token...")
            token_ok = await self._refresh_access_token()
            if not token_ok:
                yield ev.plain_result("⚠️ token 刷新失败")

            # 用 filemetas API 获取直链
            yield ev.plain_result("🔗 获取百度直链...")
            dlinks = await self._get_dlinks(list(search_dirs), files)
            if dlinks:
                out = []
                if self.show_curl_command:
                    out.append("🔧 cURL 命令:")
                    for dl in dlinks:
                        fn = dl["name"].replace('"', '\\"')
                        cmd = (
                            'curl -L -o "'
                            + fn
                            + '" -H "User-Agent:pan.baidu.com" "'
                            + dl["dlink"]
                            + '"'
                        )
                        out.append("📄 " + dl["name"] + ":\n```\n" + cmd + "\n```")
                # 移动文件夹到 /来自Bot
                move_msg = ""
                yield ev.plain_result("📁 移动文件夹到 " + self.save_dir + "...")
                # 先移动实际转存目录（如 /2024/202402/20240205）
                if (
                    save_dir != self.save_dir
                    and not save_dir.startswith("/来自Bot")
                    and "/sharelink" not in save_dir
                ):
                    move_ok = await self._move_single_dir(save_dir, self.save_dir)
                    if move_ok:
                        move_msg += (
                            "\n\n✅ " + save_dir + " 已移动到 " + self.save_dir
                        )

                # 再移动 sharelink 文件夹
                move_ok = await self._move_folder("", self.save_dir)
                if move_ok:
                    move_msg += "\n\n✅ sharelink 文件夹已移动到 " + self.save_dir
                elif not move_msg:
                    move_msg = "\n\n⚠️ 移动失败"

                # 清理空日期目录
                if has_actual_dir and save_dir and "/sharelink" not in save_dir:
                    await self._cleanup_date_dirs(save_dir)
                # 清理 /来自Bot 中的过期文件
                await self._cleanup_old_files()

                # 合并输出：直链 + 移动结果
                yield ev.plain_result("\n\n".join(out) + move_msg)
                return
            else:
                yield ev.plain_result("⚠️ 获取直链失败")

        # 即使没有获取直链，也尝试移动文件夹
        yield ev.plain_result("📁 移动文件夹到 " + self.save_dir + "...")
        move_ok = await self._move_folder("", self.save_dir)
        if move_ok:
            yield ev.plain_result("✅ 文件夹已移动到 " + self.save_dir)
            save_dir = self.save_dir

        # 清理任务
        if has_actual_dir and save_dir and "/sharelink" not in save_dir:
            await self._cleanup_date_dirs(save_dir)
        # 清理 /来自Bot 中的过期文件
        await self._cleanup_old_files()

        yield ev.plain_result("💡 文件已转存，路径: " + save_dir)

    # ---- 从 OpenList 获取凭证并刷新 token ----
    async def _refresh_access_token(self) -> bool:
        """从 OpenList 加载百度 AccessToken"""
        try:
            async with aiohttp.ClientSession() as sess:
                r = await sess.post(
                    self.openlist_url + "/api/auth/login",
                    json={
                        "username": self.openlist_user,
                        "password": self.openlist_pass,
                    },
                    allow_redirects=False,
                    timeout=10,
                )
                admin_token = (await r.json()).get("data", {}).get("token", "")

                r2 = await sess.get(
                    self.openlist_url + "/api/admin/storage/list",
                    headers={"Authorization": admin_token},
                    timeout=10,
                )
                for s in (await r2.json()).get("data", {}).get("content", []):
                    if "Baidu" in s.get("driver", ""):
                        a = json.loads(s.get("addition", "{}"))
                        self._access_token = a.get("AccessToken", "")
                        logger.info(
                            f"[token] 加载 AccessToken: {self._access_token[:25]}..."
                        )
                        return bool(self._access_token)
        except Exception as e:
            logger.error(f"[token] 加载失败: {e}")
        return False

    def _get_dlinks_sync(self, search_dirs: list, file_names: list = None) -> list:
        s = cffi_requests.Session(impersonate="chrome120")
        at = self._access_token
        all_files = []

        def _list_dir(dir_path, depth=0):
            """递归列出目录下所有文件（最大深度 3 层）"""
            if depth > 3:
                logger.warning(f"[dlink] 跳过深层目录: {dir_path}")
                return
            try:
                encoded_path = urllib.parse.quote(dir_path)
                url = f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded_path}&dlink=1&web=1&app_id=250528&access_token={at}"
                r = s.get(url, timeout=15)
                data = r.json()
                logger.info(
                    f"[dlink] list {dir_path}: errno={data.get('errno')}, count={len(data.get('list', []))}"
                )
                if data.get("errno") != 0:
                    return
                for f in data.get("list", []):
                    if f.get("isdir"):
                        _list_dir(f.get("path", ""), depth + 1)
                    else:
                        all_files.append(f)
            except Exception as e:
                logger.warning("[dlink] list error: " + str(e))

        for d in search_dirs:
            _list_dir(d)

        # 如果有文件名列表，只保留匹配的
        if file_names:
            # 提取纯文件名（去掉路径前缀）
            name_set = set()
            for n in file_names:
                name_set.add(n.split("/")[-1] if "/" in n else n)
            all_files = [f for f in all_files if f.get("server_filename") in name_set]

        fsids = [f["fs_id"] for f in all_files]
        nmap = {f["fs_id"]: f.get("server_filename", "?") for f in all_files}

        if not fsids:
            logger.warning("[dlink] 未找到文件, all_files=" + str(len(all_files)))
            return []

        # filemetas 获取 dlink
        r2 = s.get(
            f"https://pan.baidu.com/rest/2.0/xpan/multimedia?method=filemetas&dlink=1&fsids={json.dumps(fsids)}&access_token={at}",
            timeout=15,
        )
        d2 = r2.json()

        dlinks = []
        logger.info(
            "[dlink] filemetas errno="
            + str(d2.get("errno"))
            + ", list_len="
            + str(len(d2.get("list", [])))
        )
        if d2.get("errno") == 0:
            for f in d2.get("list", []):
                dl = f.get("dlink", "")
                if dl:
                    dl = dl + "&access_token=" + at
                    dlinks.append(
                        {
                            "name": f.get(
                                "server_filename", nmap.get(f.get("fs_id"), "?")
                            ),
                            "dlink": dl,
                        }
                    )
        return dlinks


    def _scan_files_sync(
        self,
        at,
        scan_files,
        save_dir_param="/来自Bot",
        actual_dir=None,
        extra_dirs=None,
        has_actual_dir=False,
        min_mtime=0,
    ):
        """同步扫描百度网盘文件（在线程池中调用）"""
        files = []
        save_dir = save_dir_param

        try:
            s = cffi_requests.Session(impersonate="chrome120")

            # 要扫描的目录列表（优先扫实际目录）
            dirs_to_scan = []
            if actual_dir:
                dirs_to_scan.append(actual_dir)
            if extra_dirs:
                dirs_to_scan.extend(extra_dirs)

            # 如果没有明确的转存目录，才扫描 save_dir_param
            if not has_actual_dir and save_dir_param not in dirs_to_scan:
                dirs_to_scan.append(save_dir_param)

            # 扫描指定目录
            for scan_dir in dirs_to_scan:
                if files:
                    break
                logger.info(f"[scan] 扫描目录: {scan_dir}")
                bot_encoded = urllib.parse.quote(scan_dir, safe="/")
                bot_resp = s.get(
                    f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={bot_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                    timeout=15,
                )
                bot_data = bot_resp.json()
                logger.info(
                    f"[scan] {scan_dir} 文件数: {len(bot_data.get('list', []))}"
                )

                # 用文件名匹配（有明确目录时，只取该目录的文件）
                def _scan_dir(dir_path, depth=0):
                    nonlocal save_dir
                    if depth > 3:
                        return
                    try:
                        encoded = urllib.parse.quote(dir_path, safe="/")
                        resp = s.get(
                            f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                            timeout=15,
                        )
                        data = resp.json()
                        for f in data.get("list", []):
                            fname = f.get("server_filename", "")
                            if f.get("isdir"):
                                _scan_dir(f.get("path", ""), depth + 1)
                                continue
                            if min_mtime and f.get("server_mtime", 0) < min_mtime:
                                continue
                            if (
                                not has_actual_dir
                                and scan_files is not None
                                and fname not in scan_files
                            ):
                                continue
                            files.append(f.get("path", ""))
                            save_dir = dir_path
                            logger.info(f"[scan] 匹配到文件: {fname}")
                    except Exception as e:
                        logger.warning(f"[scan] 子目录扫描错误: {e}")

                _scan_dir(scan_dir)

                # 如果是 actual_dir 且找到了文件，不需要继续扫描其他目录
                if files and scan_dir == actual_dir:
                    break

            # 搜索根目录的 sharelink 文件夹（不管有没有明确目录都要搜）
            if not files or has_actual_dir:
                logger.info(f"[scan] 搜索根目录 sharelink")
                root_resp = s.get(
                    f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir=/&dlink=1&web=1&app_id=250528&access_token={at}",
                    timeout=15,
                )
                root_data = root_resp.json()
                for item in root_data.get("list", []):
                    if item.get("isdir") and "sharelink" in item.get("path", ""):
                        sub_encoded = urllib.parse.quote(item["path"], safe="/")
                        sub_resp = s.get(
                            f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                            timeout=15,
                        )
                        sub_data = sub_resp.json()
                        for f in sub_data.get("list", []):
                            fname = f.get("server_filename", "")
                            if f.get("isdir"):
                                continue
                            if min_mtime and f.get("server_mtime", 0) < min_mtime:
                                continue
                            if (
                                not has_actual_dir
                                and scan_files is not None
                                and fname not in scan_files
                            ):
                                continue
                            files.append(f.get("path", ""))
                            save_dir = item["path"]
                            logger.info(
                                f"[scan] 匹配到文件: {fname} (在 {item['path']})"
                            )

            # 搜索根目录的其他文件夹（可能创建日期目录如 /2024 或 /分享等）
            if has_actual_dir:
                logger.info(f"[scan] 搜索根目录其他文件夹")
                for item in root_data.get("list", []):
                    path = item.get("path", "")
                    if (
                        item.get("isdir")
                        and "/sharelink" not in path
                        and path not in ("/来自Bot", "/apps")
                    ):
                        try:
                            encoded = urllib.parse.quote(path, safe="/")
                            sub_resp = s.get(
                                f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                                timeout=15,
                            )
                            sub_data = sub_resp.json()
                            for f in sub_data.get("list", []):
                                fname = f.get("server_filename", "")
                                if f.get("isdir"):
                                    # 递归进入子目录（最多 3 层）
                                    sub2_encoded = urllib.parse.quote(
                                        f["path"], safe="/"
                                    )
                                    sub2_resp = s.get(
                                        f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub2_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                                        timeout=15,
                                    )
                                    sub2_data = sub2_resp.json()
                                    for f2 in sub2_data.get("list", []):
                                        if f2.get("isdir"):
                                            sub3_encoded = urllib.parse.quote(
                                                f2["path"], safe="/"
                                            )
                                            sub3_resp = s.get(
                                                f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub3_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                                                timeout=15,
                                            )
                                            sub3_data = sub3_resp.json()
                                            for f3 in sub3_data.get("list", []):
                                                if not f3.get("isdir"):
                                                    if (
                                                        min_mtime
                                                        and f3.get("server_mtime", 0)
                                                        >= min_mtime
                                                    ):
                                                        files.append(f3.get("path", ""))
                                                        save_dir = f2["path"]
                                                        logger.info(
                                                            f"[scan] 匹配到文件(日期目录): {f3.get('server_filename', '')}"
                                                        )
                                        else:
                                            if (
                                                min_mtime
                                                and f2.get("server_mtime", 0)
                                                >= min_mtime
                                            ):
                                                files.append(f2.get("path", ""))
                                                save_dir = f["path"]
                                                logger.info(
                                                    f"[scan] 匹配到文件(日期目录): {f2.get('server_filename', '')}"
                                                )
                                else:
                                    if (
                                        min_mtime
                                        and f.get("server_mtime", 0) >= min_mtime
                                    ):
                                        files.append(f.get("path", ""))
                                        save_dir = path
                                        logger.info(
                                            f"[scan] 匹配到文件(日期目录): {fname}"
                                        )
                        except Exception as e:
                            logger.warning(f"[scan] 日期目录扫描错误: {e}")

            # 搜索 /来自Bot 的子目录
            if not files:
                logger.info(f"[scan] 根目录没找到，搜索子目录")
                for item in bot_data.get("list", []):
                    if item.get("isdir") and "sharelink" in item.get("path", ""):
                        sub_encoded = urllib.parse.quote(item["path"], safe="/")
                        sub_resp = s.get(
                            f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                            timeout=15,
                        )
                        sub_data = sub_resp.json()
                        for f in sub_data.get("list", []):
                            fname = f.get("server_filename", "")
                            if scan_files is None or fname in scan_files:
                                if min_mtime and f.get("server_mtime", 0) < min_mtime:
                                    continue
                                files.append(f.get("path", ""))
                                save_dir = item["path"]
                                logger.info(
                                    f"[scan] 匹配到文件: {fname} (在 {item['path']})"
                                )
        except Exception as e:
            logger.error(f"[scan] 扫描失败: {e}")

        logger.info(f"[scan] 最终文件: {files}, 目录: {save_dir}")
        return files, save_dir

    async def _cleanup_date_dirs(self, dir_path: str):
        """用百度 API 逐级删除空日期目录（OpenList 可能无法访问这些路径）"""
        if not self._access_token:
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, self._cleanup_date_dirs_sync, dir_path, self._access_token
            )
        except Exception as e:
            logger.warning(f"[cleanup] 清理目录失败: {e}")

    def _cleanup_date_dirs_sync(self, dir_path: str, at: str):
        """同步清理日期目录（在线程池中执行）"""
        try:
            s = cffi_requests.Session(impersonate="chrome120")
            parts = dir_path.strip("/").split("/")
            # 从最深目录向上逐级尝试删除
            for i in range(len(parts), 0, -1):
                path = "/" + "/".join(parts[:i])
                filelist = json.dumps([path])
                url = f"https://pan.baidu.com/rest/2.0/xpan/file?method=filemanager&opera=delete&access_token={at}"
                resp = s.post(
                    url,
                    data={"async": 0, "filelist": filelist, "ondup": "fail"},
                    timeout=15,
                )
                data = resp.json()
                logger.info(
                    f"[cleanup] 删除 {path}: errno={data.get('errno')}, info={data.get('info')}"
                )
        except Exception as e:
            logger.warning(f"[cleanup] 删除目录异常: {e}")

    async def _cleanup_old_files(self):
        """清理 /来自Bot 中超过保留时长的文件"""
        if not self.file_retention_hours or not self._access_token:
            return
        try:
            loop = asyncio.get_running_loop()
            deleted = await loop.run_in_executor(
                None, self._cleanup_old_files_sync, self._access_token
            )
            if deleted:
                logger.info(f"[cleanup] 清理了 {deleted} 个过期文件")
        except Exception as e:
            logger.warning(f"[cleanup] 清理旧文件失败: {e}")

    def _cleanup_old_files_sync(self, at: str) -> int:
        """同步清理过期文件（在线程池中执行）"""
        cutoff = int(time.time()) - self.file_retention_hours * 3600
        all_files = []

        try:
            s = cffi_requests.Session(impersonate="chrome120")

            def _list_recursive(dir_path, depth=0):
                if depth > 3:
                    return
                try:
                    encoded = urllib.parse.quote(dir_path, safe="/")
                    resp = s.get(
                        f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                        timeout=15,
                    )
                    data = resp.json()
                    for f in data.get("list", []):
                        if f.get("isdir"):
                            _list_recursive(f.get("path", ""), depth + 1)
                        else:
                            mtime = f.get("server_mtime", 0)
                            if mtime < cutoff:
                                all_files.append(f.get("path", ""))
                except Exception as e:
                    logger.warning(f"[cleanup] 列出目录失败: {e}")

            _list_recursive(self.save_dir)

            if not all_files:
                return 0

            # 批量删除过期文件
            logger.info(f"[cleanup] 发现 {len(all_files)} 个过期文件，开始删除")
            # 每批最多删 100 个
            for i in range(0, len(all_files), 100):
                batch = all_files[i : i + 100]
                filelist = json.dumps(batch)
                url = f"https://pan.baidu.com/rest/2.0/xpan/file?method=filemanager&opera=delete&access_token={at}"
                resp = s.post(
                    url,
                    data={"async": 0, "filelist": filelist, "ondup": "fail"},
                    timeout=30,
                )
                data = resp.json()
                logger.info(
                    f"[cleanup] 批次 {i // 100 + 1}: errno={data.get('errno')}, info={data.get('info')}"
                )

            return len(all_files)
        except Exception as e:
            logger.warning(f"[cleanup] 清理旧文件异常: {e}")
            return 0


    # ==================== 内置转存（BDUSS Cookie） ====================

    async def _list_share_files(self, surl: str, pwd: str) -> dict:
        """列出分享文件（不转存）"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._list_share_files_sync, surl, pwd
            )
        except Exception as e:
            logger.error(f"[builtin] 列出分享文件失败: {e}")
            return {"success": False, "error": str(e)}

    async def _transfer_selected_files(self, surl: str, pwd: str, selected_indices: list) -> dict:
        """转存选中的文件"""
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._transfer_files_sync, surl, pwd, selected_indices
            )
        except Exception as e:
            logger.error(f"[builtin] 转存失败: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    def _parse_cookie_string(cookie_str: str) -> dict:
        """解析 Cookie 字符串为字典，如 'BDUSS=xxx; STOKEN=yyy' -> {BDUSS: xxx, STOKEN: yyy}"""
        cookies = {}
        if not cookie_str:
            return cookies
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookies[key.strip()] = value.strip()
        return cookies

    def _list_share_files_sync(self, surl: str, pwd: str) -> dict:
        """列出分享中的所有文件（不转存）

        流程:
        1. 用 Cookie 访问分享链接，验证密码（如有）
        2. 解析分享页面获取文件列表
        3. 递归列出文件夹内容，收集所有文件信息

        Returns:
            dict: {success, files: [{name, fs_id, path}], uk, share_id, bdstoken, share_url}
        """
        try:
            s = cffi_requests.Session(impersonate="chrome120")

            # 解析 Cookie
            cookies = self._parse_cookie_string(self.baidu_cookies)
            if not cookies.get("BDUSS"):
                return {"success": False, "error": "Cookie 中缺少 BDUSS，请检查 Cookie 是否正确"}
            if not cookies.get("STOKEN"):
                logger.warning("[builtin] Cookie 中缺少 STOKEN，部分分享可能无法转存")
            for k, v in cookies.items():
                if v:
                    s.cookies.set(k, v)

            share_url = f"https://pan.baidu.com/s/1{surl}"
            init_url = f"https://pan.baidu.com/share/init?surl={surl}"

            # ---- 步骤1: 验证密码（如有）----
            if pwd:
                logger.info(f"[builtin] 验证密码: surl={surl}")
                params = {
                    "surl": surl,
                    "t": str(int(time.time() * 1000)),
                    "channel": "chunlei",
                    "web": "1",
                    "bdstoken": "null",
                    "clienttype": "0",
                    "app_id": "250528",
                }
                data = {"pwd": pwd, "vcode": "", "vcode_str": ""}
                headers = {"Referer": init_url}
                resp = s.post(
                    "https://pan.baidu.com/share/verify",
                    params=params,
                    data=data,
                    headers=headers,
                    timeout=15,
                )
                result = resp.json()
                logger.info(f"[builtin] 密码验证结果: errno={result.get('errno')}")
                if result.get("errno") != 0:
                    return {
                        "success": False,
                        "error": f"密码验证失败 (errno={result.get('errno')})",
                    }

            # ---- 步骤2: 访问分享页面，解析文件列表 ----
            logger.info(f"[builtin] 访问分享页面: {share_url}")
            resp = s.get(share_url, timeout=15)
            html = resp.text

            match = re.search(r"(?:yunData\.setData|locals\.mset)\(", html)
            if not match:
                return {"success": False, "error": "无法解析分享页面，可能 Cookie 已过期或分享已失效"}

            start = match.end()
            brace_count = 0
            end = start
            for i, c in enumerate(html[start:], start):
                if c == "{":
                    brace_count += 1
                elif c == "}":
                    brace_count -= 1
                    if brace_count == 0:
                        end = i + 1
                        break

            try:
                shared_data = json.loads(html[start:end])
            except json.JSONDecodeError:
                return {"success": False, "error": "解析分享数据失败"}

            uk = int(shared_data.get("share_uk") or shared_data.get("uk", 0))
            share_id = shared_data.get("shareid")
            bdstoken = shared_data.get("bdstoken", "") or ""

            if not uk or not share_id:
                return {"success": False, "error": "无法获取分享信息 (uk/shareid 为空)"}

            file_list_raw = shared_data.get("file_list", {})
            if isinstance(file_list_raw, list):
                root_files = file_list_raw
            elif isinstance(file_list_raw, dict):
                root_files = file_list_raw.get("list", [])
            else:
                root_files = []

            if not root_files:
                return {"success": False, "error": "分享中没有文件"}

            logger.info(f"[builtin] 分享根目录有 {len(root_files)} 项, uk={uk}, share_id={share_id}")

            # ---- 步骤3: 递归收集所有文件信息 ----
            all_files = []  # [{name, fs_id, path}]

            def _collect_files(file_items):
                for f in file_items:
                    if f.get("isdir") == 1:
                        dir_path = f.get("path", "")
                        logger.info(f"[builtin] 列出共享目录: {dir_path}")
                        sub_files = self._list_shared_dir_builtin(
                            s, dir_path, uk, share_id
                        )
                        _collect_files(sub_files)
                    else:
                        fs_id = f.get("fs_id")
                        if fs_id:
                            name = f.get("server_filename") or f.get("path", "").split("/")[-1]
                            path = f.get("path", name)
                            all_files.append({"name": name, "fs_id": fs_id, "path": path})
                            logger.info(f"[builtin] 记录文件: {name}")

            _collect_files(root_files)

            if not all_files:
                return {"success": False, "error": "没有可转存的文件（可能全是空文件夹）"}

            logger.info(f"[builtin] 共 {len(all_files)} 个文件")

            return {
                "success": True,
                "files": all_files,
                "uk": uk,
                "share_id": share_id,
                "bdstoken": bdstoken,
                "share_url": share_url,
            }

        except Exception as e:
            logger.error(f"[builtin] 列出分享文件异常: {e}")
            return {"success": False, "error": str(e)}

    def _transfer_files_sync(self, surl: str, pwd: str, selected_indices: list) -> dict:
        """转存选中的文件

        Args:
            surl: 分享 surl
            pwd: 提取码
            selected_indices: 选中的文件索引列表（对应 _list_share_files 返回的 files 列表）
        """
        try:
            # 先列出文件获取 uk/share_id/bdstoken
            lr = self._list_share_files_sync(surl, pwd)
            if not lr.get("success"):
                return lr

            all_files = lr["files"]
            uk = lr["uk"]
            share_id = lr["share_id"]
            bdstoken = lr["bdstoken"]
            share_url = lr["share_url"]

            # 筛选选中的文件
            selected = []
            for idx in selected_indices:
                if 0 <= idx < len(all_files):
                    selected.append(all_files[idx])

            if not selected:
                return {"success": False, "error": "没有选中任何文件"}

            fs_ids = [f["fs_id"] for f in selected]
            file_names = [f["name"] for f in selected]

            logger.info(f"[builtin] 转存 {len(fs_ids)} 个选中文件: {file_names}")

            # 创建 session 并设置 cookie
            s = cffi_requests.Session(impersonate="chrome120")
            cookies = self._parse_cookie_string(self.baidu_cookies)
            for k, v in cookies.items():
                if v:
                    s.cookies.set(k, v)

            # 重新验证密码（transfer 需要验证后的 cookie）
            if pwd:
                init_url = f"https://pan.baidu.com/share/init?surl={surl}"
                params = {
                    "surl": surl,
                    "t": str(int(time.time() * 1000)),
                    "channel": "chunlei",
                    "web": "1",
                    "bdstoken": "null",
                    "clienttype": "0",
                    "app_id": "250528",
                }
                s.post(
                    "https://pan.baidu.com/share/verify",
                    params=params,
                    data={"pwd": pwd, "vcode": "", "vcode_str": ""},
                    headers={"Referer": init_url},
                    timeout=15,
                )

            # 确保目标目录存在
            save_dir = self.save_dir
            self._ensure_dir_exists(s, save_dir)

            # 调用 transfer API
            transfer_params = {
                "shareid": str(share_id),
                "from": str(uk),
                "bdstoken": bdstoken if bdstoken else "null",
                "channel": "chunlei",
                "clienttype": "0",
                "web": "1",
                "app_id": "250528",
            }
            transfer_data = {
                "fsidlist": json.dumps(fs_ids),
                "path": save_dir,
            }
            transfer_headers = {
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": "https://pan.baidu.com",
                "Referer": share_url,
            }

            logger.info(f"[builtin] 转存到 {save_dir}")
            resp = s.post(
                "https://pan.baidu.com/share/transfer",
                params=transfer_params,
                data=transfer_data,
                headers=transfer_headers,
                timeout=30,
            )
            result = resp.json()
            logger.info(f"[builtin] 转存结果: errno={result.get('errno')}")

            errno = result.get("errno", -1)

            if errno == 0:
                transferred = []
                for item in result.get("info", []):
                    if item.get("errno") == 0:
                        transferred.append(item.get("path", "").split("/")[-1])
                file_names = transferred if transferred else file_names
                return {
                    "success": True,
                    "files": file_names,
                    "save_dir": save_dir,
                }
            elif errno in (4, 12, -33):
                logger.info(f"[builtin] 文件已存在(errno={errno})")
                return {
                    "success": True,
                    "files": file_names,
                    "save_dir": save_dir,
                    "existed": True,
                }
            elif errno == -20:
                return {"success": False, "error": "转存失败: 可能 Cookie 已过期，请更新 BDUSS/STOKEN"}
            elif errno == -130:
                return {"success": False, "error": "转存失败: 分享已失效或被取消"}
            else:
                partial = [item for item in result.get("info", []) if item.get("errno") == 0]
                if partial:
                    file_names = [item.get("path", "").split("/")[-1] for item in partial]
                    return {"success": True, "files": file_names, "save_dir": save_dir}
                return {"success": False, "error": f"转存失败 (errno={errno})"}

        except Exception as e:
            logger.error(f"[builtin] 转存异常: {e}")
            return {"success": False, "error": str(e)}

    def _list_shared_dir_builtin(self, session, dir_path: str, uk: int, share_id: int) -> list:
        """列出分享目录中的文件（分页获取，最多 3 层深度）"""
        all_files = []
        page = 1
        while True:
            params = {
                "channel": "chunlei",
                "clienttype": "0",
                "web": "1",
                "page": str(page),
                "num": "100",
                "dir": dir_path,
                "t": str(random.random()),
                "uk": str(uk),
                "shareid": str(share_id),
                "desc": "1",
                "order": "other",
                "bdstoken": "null",
                "showempty": "0",
                "app_id": "250528",
            }
            try:
                resp = session.get(
                    "https://pan.baidu.com/share/list",
                    params=params,
                    timeout=15,
                )
                data = resp.json()
                if data.get("errno") != 0:
                    logger.warning(f"[builtin] 列出目录失败: {dir_path}, errno={data.get('errno')}")
                    break
                file_list = data.get("list", [])
                all_files.extend(file_list)
                if len(file_list) < 100:
                    break
                page += 1
                if page > 10:  # 安全限制，最多 1000 个文件
                    break
            except Exception as e:
                logger.warning(f"[builtin] 列出目录异常: {e}")
                break
        return all_files

    def _get_bdstoken(self, session) -> str:
        """从百度网盘首页获取当前用户的 bdstoken"""
        try:
            resp = session.get("https://pan.baidu.com/disk/home", timeout=15)
            html = resp.text
            m = re.search(r'bdstoken["\':\s]+([0-9a-f]{32})', html)
            if m:
                return m.group(1)
            # 尝试另一种格式
            m = re.search(r'"bdstoken":"?([0-9a-f]{32})"?', html)
            if m:
                return m.group(1)
        except Exception as e:
            logger.warning(f"[builtin] 获取 bdstoken 失败: {e}")
        return ""

    def _ensure_dir_exists(self, session, dir_path: str):
        """确保网盘目录存在，逐级创建（类似 mkdir -p）"""
        if not dir_path or dir_path == "/":
            return

        # 先获取当前用户的 bdstoken
        bdstoken = self._get_bdstoken(session)

        parts = dir_path.strip("/").split("/")
        current = ""
        for part in parts:
            current = current + "/" + part
            # 尝试创建目录（已存在则忽略）
            try:
                # 方式1: 用 create 接口（不需要 bdstoken）
                params = {
                    "a": "mkdir",
                    "channel": "chunlei",
                    "web": "1",
                    "app_id": "250528",
                    "bdstoken": bdstoken or "null",
                    "clienttype": "0",
                }
                data = {"path": current, "isdir": "1"}
                resp = session.post(
                    "https://pan.baidu.com/api/filemanager",
                    params=params,
                    data=data,
                    timeout=10,
                )
                result = resp.json()
                errno = result.get("errno")
                # errno 0=成功, -8=目录已存在, 都算正常
                if errno in (0, -8):
                    logger.info(f"[builtin] 目录就绪: {current}")
                elif errno == 2 and bdstoken:
                    # bdstoken 无效，尝试用 create 接口
                    logger.info(f"[builtin] filemanager 失败(errno=2)，尝试 create 接口: {current}")
                    resp2 = session.post(
                        "https://pan.baidu.com/api/create",
                        params={"a": "mkdir", "channel": "chunlei", "web": "1", "app_id": "250528", "clienttype": "0"},
                        data={"path": current, "isdir": "1"},
                        timeout=10,
                    )
                    result2 = resp2.json()
                    if result2.get("errno") in (0, -8):
                        logger.info(f"[builtin] 目录就绪(create): {current}")
                    else:
                        logger.warning(f"[builtin] 创建目录失败: {current}, errno={result2.get('errno')}")
                else:
                    logger.warning(f"[builtin] 创建目录失败: {current}, errno={errno}")
            except Exception as e:
                logger.warning(f"[builtin] 创建目录异常: {current}, {e}")

    async def _get_dlinks(self, save_dir: str, file_names: list = None) -> list:
        loop = asyncio.get_running_loop()

        return await loop.run_in_executor(
            None, self._get_dlinks_sync, save_dir, file_names
        )

    # ---- 移动文件夹 ----

    async def _move_single_dir(self, from_dir: str, to_dir: str) -> bool:
        """移动单个目录到目标目录"""
        if not self.openlist_url or not self.openlist_user:
            logger.warning("[move] 未配置 OpenList，跳过移动")
            return False

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._move_single_dir_sync, from_dir, to_dir
            )
        except Exception as e:
            logger.error(f"[move] 移动失败: {e}")
            return False

    def _move_single_dir_sync(self, from_dir: str, to_dir: str) -> bool:
        """同步移动单个目录"""
        try:
            with cffi_requests.Session(impersonate="chrome120") as s:
                # 登录 OpenList
                login_resp = s.post(
                    f"{self.openlist_url}/api/auth/login",
                    json={
                        "username": self.openlist_user,
                        "password": self.openlist_pass,
                    },
                    allow_redirects=False,
                    timeout=15,
                )
                admin_token = login_resp.json().get("data", {}).get("token", "")
                headers = {"Authorization": admin_token}

                pan_prefix = "/百度"

                # 获取源目录里的文件
                src_path = f"{pan_prefix}{from_dir}"
                resp = s.post(
                    f"{self.openlist_url}/api/fs/list",
                    headers=headers,
                    json={
                        "path": src_path,
                        "page": 1,
                        "per_page": 100,
                        "refresh": True,
                    },
                    timeout=10,
                )
                data = resp.json()

                if data.get("code") != 200:
                    logger.error(f"[move] 获取目录失败: {data}")
                    return False

                files = [
                    f.get("name") for f in (data.get("data", {}).get("content") or [])
                ]

                if not files:
                    logger.info(f"[move] 目录为空: {from_dir}")
                    return True

                logger.info(f"[move] 移动 {from_dir} -> {to_dir}, 文件: {files}")

                # 创建目标子目录
                folder_name = from_dir.rstrip("/").split("/")[-1]
                dst_path = f"{pan_prefix}{to_dir}/{folder_name}"
                s.post(
                    f"{self.openlist_url}/api/fs/mkdir",
                    headers=headers,
                    json={"path": dst_path},
                    timeout=10,
                )

                # 移动文件
                move_resp = s.post(
                    f"{self.openlist_url}/api/fs/move",
                    headers=headers,
                    json={"src_dir": src_path, "dst_dir": dst_path, "names": files},
                    timeout=30,
                )
                result = move_resp.json()
                logger.info(f"[move] 移动结果: {result}")

                if result.get("code") == 200:
                    # 删除源目录（从正确的父目录中删除，并递归清理空目录）
                    parts = from_dir.strip("/").split("/")
                    # 从最深目录向上逐级删除空目录
                    for i in range(len(parts), 0, -1):
                        parent = "/" + "/".join(parts[: i - 1]) if i > 1 else ""
                        name = parts[i - 1]
                        parent_path = pan_prefix + parent
                        s.post(
                            f"{self.openlist_url}/api/fs/remove",
                            headers=headers,
                            json={"dir": parent_path, "names": [name]},
                            timeout=10,
                        )
                        logger.info(f"[move] 删除目录: {parent_path}/{name}")
                    return True

                return False

        except Exception as e:
            logger.error(f"[move] 移动失败: {e}")
            return False

    async def _move_folder(self, from_dir: str, to_dir: str) -> bool:
        """用 OpenList API 移动整个文件夹"""
        if not self.openlist_url or not self.openlist_user:
            logger.warning("[move] 未配置 OpenList，跳过移动")
            return False

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._move_folder_sync, from_dir, to_dir
            )
        except Exception as e:
            logger.error(f"[move] 移动失败: {e}")
            return False

    def _move_folder_sync(self, from_dir: str, to_dir: str) -> bool:
        """扫描百度网盘找最近创建的 sharelink 文件夹并移动"""
        try:
            s = cffi_requests.Session(impersonate="chrome120")

            # 登录 OpenList
            login_resp = s.post(
                f"{self.openlist_url}/api/auth/login",
                json={"username": self.openlist_user, "password": self.openlist_pass},
                allow_redirects=False,
                timeout=15,
            )
            admin_token = login_resp.json().get("data", {}).get("token", "")

            if not admin_token:
                logger.error("[move] OpenList 登录失败")
                return False

            headers = {"Authorization": admin_token}
            pan_prefix = "/百度"

            # 扫描百度网盘根目录，找最近的 sharelink 文件夹
            resp = s.post(
                f"{self.openlist_url}/api/fs/list",
                headers=headers,
                json={
                    "path": f"{pan_prefix}/",
                    "page": 1,
                    "per_page": 100,
                    "refresh": True,
                },
                timeout=10,
            )
            data = resp.json()

            if data.get("code") != 200:
                logger.error(f"[move] 获取根目录失败: {data}")
                return False

            content_list = data.get("data", {}).get("content") or []

            # 找所有 sharelink 开头的文件夹
            sharelink_dirs = []
            for item in content_list:
                name = item.get("name", "")
                if name.startswith("sharelink") and item.get("is_dir"):
                    sharelink_dirs.append(
                        {"name": name, "modified": item.get("modified", 0)}
                    )

            if not sharelink_dirs:
                logger.info("[move] 没有找到 sharelink 文件夹，文件可能已在目标目录")
                return True

            # 按修改时间排序，找最近的
            sharelink_dirs.sort(key=lambda x: x["modified"], reverse=True)
            newest_dir = sharelink_dirs[0]

            logger.info(
                f"[move] 找到最新 sharelink 文件夹: {newest_dir['name']} (修改时间: {newest_dir['modified']})"
            )

            # 移动这个文件夹到目标目录
            src_path = f"{pan_prefix}/{newest_dir['name']}"
            dst_path = f"{pan_prefix}{to_dir}/{newest_dir['name']}"

            logger.info(f"[move] 移动: {src_path} -> {dst_path}")

            # 获取源目录里的所有文件
            resp2 = s.post(
                f"{self.openlist_url}/api/fs/list",
                headers=headers,
                json={"path": src_path, "page": 1, "per_page": 100, "refresh": True},
                timeout=10,
            )
            data2 = resp2.json()

            files = [
                f.get("name") for f in (data2.get("data", {}).get("content") or [])
            ]

            if not files:
                logger.warning("[move] 源目录为空")
                return False

            logger.info(f"[move] 源目录文件: {files}")

            # 创建目标子目录
            s.post(
                f"{self.openlist_url}/api/fs/mkdir",
                headers=headers,
                json={"path": dst_path},
                timeout=10,
            )

            # 移动所有文件
            move_resp = s.post(
                f"{self.openlist_url}/api/fs/move",
                headers=headers,
                json={"src_dir": src_path, "dst_dir": dst_path, "names": files},
                timeout=30,
            )
            result = move_resp.json()
            logger.info(f"[move] 移动结果: {result}")

            code = result.get("code")
            msg = result.get("message", "")
            if code == 200:
                # 删除空的源目录
                s.post(
                    f"{self.openlist_url}/api/fs/remove",
                    headers=headers,
                    json={"dir": f"{pan_prefix}", "names": [newest_dir["name"]]},
                    timeout=10,
                )
                logger.info(f"[move] 已删除源目录: {newest_dir['name']}")
                return True
            if "exists" in msg:
                logger.info(f"[move] 文件已存在，跳过移动")
                # 文件已存在说明之前移过了，也删除源目录
                s.post(
                    f"{self.openlist_url}/api/fs/remove",
                    headers=headers,
                    json={"dir": f"{pan_prefix}", "names": [newest_dir["name"]]},
                    timeout=10,
                )
                return True
            return False

        except Exception as e:
            logger.error(f"[move] 移动失败: {e}")
            return False

    def _move_files_sync(self, files: list, from_dir: str, to_dir: str) -> bool:
        """用 OpenList API 移动文件"""
        try:
            # 获取 OpenList token
            s = cffi_requests.Session(impersonate="chrome120")

            login_resp = s.post(
                f"{self.openlist_url}/api/auth/login",
                json={"username": self.openlist_user, "password": self.openlist_pass},
                allow_redirects=False,
                timeout=15,
            )
            admin_token = login_resp.json().get("data", {}).get("token", "")

            if not admin_token:
                logger.error("[move] OpenList 登录失败")
                return False

            headers = {"Authorization": admin_token}

            # OpenList 百度网盘挂载路径前缀
            pan_prefix = "/百度"

            # 构建源目录和目标目录路径
            src_dir = f"{pan_prefix}{from_dir}"
            dst_dir = f"{pan_prefix}{to_dir}"

            # 提取文件名列表
            names = []
            for f in files:
                fname = f.split("/")[-1] if "/" in f else f
                names.append(fname)

            if not names:
                return False

            logger.info(f"[move] 移动文件: {src_dir} -> {dst_dir}, 文件: {names}")

            # 调用 OpenList 移动 API
            resp = s.post(
                f"{self.openlist_url}/api/fs/move",
                headers=headers,
                json={"src_dir": src_dir, "dst_dir": dst_dir, "names": names},
                timeout=30,
            )
            result = resp.json()
            logger.info(f"[move] 移动结果: {result}")

            # 成功或文件已存在都算成功
            code = result.get("code")
            msg = result.get("message", "")
            if code == 200:
                return True
            if "exists" in msg:
                # 文件已存在，检查是否真的在目标目录
                logger.info(f"[move] 文件已存在，跳过移动")
                return True
            return False

        except Exception as e:
            logger.error(f"[move] 移动失败: {e}")
            return False


