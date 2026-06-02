"""
百度网盘 cURL 下载助手 v7
baidu-autosave 转存 + refresh_token 刷新 + filemetas API 获取直链
"""

from __future__ import annotations

import asyncio
import time
import json
import re
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
    for p in [r"提取码[：:\s]*([a-zA-Z0-9]{4})", r"密码[：:\s]*([a-zA-Z0-9]{4})",
              r"pwd[=：:\s]*([a-zA-Z0-9]{4})", r"[:\s]([a-zA-Z0-9]{4})\s*$"]:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            r["pwd"] = m.group(1)
            break
    return r
class BaiduCurlPlugin(Star):
    _RE = re.compile(r"https?://pan\.baidu\.com/s/[a-zA-Z0-9_-]+")
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        cfg = dict(config or {})
        self.allow_sessions: list = cfg.get("allow_sessions", [])
        # baidu-autosave
        self.autosave_url: str = cfg.get("autosave_url", "").rstrip("/")
        self.autosave_user: str = cfg.get("autosave_user", "")
        self.autosave_pass: str = cfg.get("autosave_pass", "")
        self.autosave_dir: str = cfg.get("autosave_dir", "/来自Bot")
        # OpenList (用于获取 refresh_token 等凭证)
        self.openlist_url: str = cfg.get("openlist_url", "").rstrip("/")
        self.openlist_user: str = cfg.get("openlist_user", "")
        self.openlist_pass: str = cfg.get("openlist_pass", "")
        self.bduss: str = cfg.get("bduss", "")
        # 缓存
        self._access_token: str = ""
        self._token_expire: float = 0  # token 过期时间戳
        self._refresh_token: str = ""
        self._client_id: str = ""
        self._client_secret: str = ""
    async def terminate(self):
        pass

    @filter.event_message_type(filter.EventMessageType.ALL)

    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str
        # 如果是影视转存命令，跳过（由 media_save 插件处理）
        if re.match(r"^(电影|电视剧|动漫|综艺|纪录片|movie|tv|anime|动画|转存)", text.strip()):
            return
        if not self._RE.search(text):
            return
        if self.allow_sessions and event.session_id not in self.allow_sessions:
            return
        if not self.autosave_url:
            yield event.plain_result("⚠️ 未配置 baidu-autosave")
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
        # 1. baidu-autosave 转存
        yield ev.plain_result("📦 转存中...")
        tr = await self._autosave(surl, pwd)
        if not tr.get("success"):
            yield ev.plain_result("❌ 转存失败: " + tr.get("error", "未知"))
            return

        # 2. 用文件名在百度网盘里匹配
        files = []
        save_dir = self.autosave_dir
        
        # 获取 baidu-autosave 返回的文件名（用于匹配）
        autosave_files = tr.get("files", [])
        existed = tr.get("existed", False)
        logger.info(f"[scan] 要匹配的文件: {autosave_files}, 已存在: {existed}")
        
        # 等待转存完成
        await asyncio.sleep(5)
        
        # 用百度网盘 API 扫描并匹配文件（在线程池中执行避免阻塞）
        token_ok = await self._refresh_access_token()
        if token_ok and self._access_token:
            scan_files = autosave_files if autosave_files else None
            at = self._access_token
            
            loop = asyncio.get_running_loop()
            files, save_dir = await loop.run_in_executor(
                None,
                self._scan_files_sync, at, scan_files
            )
        
        if not files:
            yield ev.plain_result("❌ 未找到转存的文件")
            return
        
        yield ev.plain_result("✅ 转存成功！\n📁 " + save_dir + "\n📄 " + (", ".join([f.split("/")[-1] for f in files])))

        # 3. 获取直链
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

            # 3. 用 filemetas API 获取直链
            yield ev.plain_result("🔗 获取百度直链...")
            dlinks = await self._get_dlinks(list(search_dirs), files)
            if dlinks:
                out = ["🔧 cURL 命令:"]
                for dl in dlinks:
                    fn = dl["name"].replace('"', '\\"')
                    cmd = 'curl -L -o "' + fn + '" -H "User-Agent:pan.baidu.com" "' + dl["dlink"] + '"'
                    out.append("📄 " + dl["name"] + ":\n```\n" + cmd + "\n```")
                # 4. 移动文件夹到 /来自Bot
                move_msg = ""
                yield ev.plain_result("📁 移动文件夹到 " + self.autosave_dir + "...")
                move_ok = await self._move_folder("", self.autosave_dir)
                if move_ok:
                    move_msg = "\n\n✅ 文件夹已移动到 " + self.autosave_dir
                else:
                    move_msg = "\n\n⚠️ 移动失败"
                
                # 5. 清理 baidu-autosave 任务
                await self._cleanup_autosave_task(surl)
                
                # 合并输出：直链 + 移动结果
                yield ev.plain_result("\n\n".join(out) + move_msg)
                return
            else:
                yield ev.plain_result("⚠️ 获取直链失败")

        # 即使没有获取直链，也尝试移动文件夹
        yield ev.plain_result("📁 移动文件夹到 " + self.autosave_dir + "...")
        move_ok = await self._move_folder("", self.autosave_dir)
        if move_ok:
            yield ev.plain_result("✅ 文件夹已移动到 " + self.autosave_dir)
            save_dir = self.autosave_dir
        
        # 清理任务
        await self._cleanup_autosave_task(surl)
        
        yield ev.plain_result("💡 文件已转存，路径: " + save_dir)

    # ---- 从 OpenList 获取凭证并刷新 token ----
    async def _refresh_access_token(self) -> bool:
        """从 OpenList 加载百度 AccessToken"""
        try:
            async with aiohttp.ClientSession() as sess:
                r = await sess.post(self.openlist_url + "/api/auth/login",
                    json={"username": self.openlist_user, "password": self.openlist_pass},
                    allow_redirects=False, timeout=aiohttp.ClientTimeout(total=10))
                admin_token = (await r.json()).get("data", {}).get("token", "")

                r2 = await sess.get(self.openlist_url + "/api/admin/storage/list",
                    headers={"Authorization": admin_token}, timeout=aiohttp.ClientTimeout(total=10))
                for s in (await r2.json()).get("data", {}).get("content", []):
                    if "Baidu" in s.get("driver", ""):
                        a = json.loads(s.get("addition", "{}"))
                        self._access_token = a.get("AccessToken", "")
                        logger.info(f"[token] 加载 AccessToken: {self._access_token[:25]}...")
                        return bool(self._access_token)
        except Exception as e:
            logger.error(f"[token] 加载失败: {e}")
        return False
    
    def _get_dlinks_sync(self, search_dirs: list, file_names: list = None) -> list:
        s = cffi_requests.Session(impersonate="chrome120")
        at = self._access_token
        all_files = []
        def _list_dir(dir_path):
            """递归列出目录下所有文件"""
            try:
                encoded_path = urllib.parse.quote(dir_path)
                url = f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={encoded_path}&dlink=1&web=1&app_id=250528&access_token={at}"
                r = s.get(url, timeout=15)
                data = r.json()
                logger.info(f"[dlink] list {dir_path}: errno={data.get('errno')}, count={len(data.get('list',[]))}")
                if data.get("errno") != 0:
                    return
                for f in data.get("list", []):
                    if f.get("isdir"):
                        _list_dir(f.get("path", ""))
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
        r2 = s.get(f"https://pan.baidu.com/rest/2.0/xpan/multimedia?method=filemetas&dlink=1&fsids={json.dumps(fsids)}&access_token={at}", timeout=15)
        d2 = r2.json()

        dlinks = []
        logger.info("[dlink] filemetas errno=" + str(d2.get("errno")) + ", list_len=" + str(len(d2.get("list", []))))
        if d2.get("errno") == 0:
            for f in d2.get("list", []):
                dl = f.get("dlink", "")
                if dl:
                    dl = dl + "&access_token=" + at
                    dlinks.append({"name": f.get("server_filename", nmap.get(f.get("fs_id"), "?")), "dlink": dl})
        return dlinks
    async def _autosave(self, surl: str, pwd: str) -> dict:
        """调用 baidu-autosave 服务转存文件（使用 curl_cffi，兼容性更好）"""
        if not self.autosave_url:
            return {"success": False, "error": "未配置 autosave_url"}
        
        try:
            
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, 
                self._autosave_sync, surl, pwd)
        except Exception as e:
            logger.error(f"[autosave] 转存失败: {e}")
            return {"success": False, "error": str(e)}
    def _scan_files_sync(self, at, scan_files):
        """同步扫描百度网盘文件（在线程池中调用）"""
        files = []
        save_dir = "/来自Bot"
        
        try:
            s = cffi_requests.Session(impersonate="chrome120")
            
            # 扫描 /来自Bot 目录
            bot_encoded = urllib.parse.quote("/来自Bot", safe="/")
            bot_resp = s.get(
                f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={bot_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                timeout=15
            )
            bot_data = bot_resp.json()
            logger.info(f"[scan] /来自Bot 目录文件数: {len(bot_data.get('list', []))}")
            
            # 用文件名匹配
            for f in bot_data.get("list", []):
                fname = f.get("server_filename", "")
                if scan_files is None or fname in scan_files:
                    files.append(f.get("path", ""))
                    save_dir = "/来自Bot"
                    logger.info(f"[scan] 匹配到文件: {fname}")
            
            # 搜索根目录的 sharelink 文件夹
            if not files:
                logger.info(f"[scan] /来自Bot 没找到，搜索根目录 sharelink")
                root_resp = s.get(
                    f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir=/&dlink=1&web=1&app_id=250528&access_token={at}",
                    timeout=15
                )
                root_data = root_resp.json()
                for item in root_data.get("list", []):
                    if item.get("isdir") and "sharelink" in item.get("path", ""):
                        sub_encoded = urllib.parse.quote(item["path"], safe="/")
                        sub_resp = s.get(
                            f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                            timeout=15
                        )
                        sub_data = sub_resp.json()
                        for f in sub_data.get("list", []):
                            fname = f.get("server_filename", "")
                            if scan_files is None or fname in scan_files:
                                files.append(f.get("path", ""))
                                save_dir = item["path"]
                                logger.info(f"[scan] 匹配到文件: {fname} (在 {item['path']})")
            
            # 搜索 /来自Bot 的子目录
            if not files:
                logger.info(f"[scan] 根目录没找到，搜索子目录")
                for item in bot_data.get("list", []):
                    if item.get("isdir") and "sharelink" in item.get("path", ""):
                        sub_encoded = urllib.parse.quote(item["path"], safe="/")
                        sub_resp = s.get(
                            f"https://pan.baidu.com/rest/2.0/xpan/file?method=list&dir={sub_encoded}&dlink=1&web=1&app_id=250528&access_token={at}",
                            timeout=15
                        )
                        sub_data = sub_resp.json()
                        for f in sub_data.get("list", []):
                            fname = f.get("server_filename", "")
                            if scan_files is None or fname in scan_files:
                                files.append(f.get("path", ""))
                                save_dir = item["path"]
                                logger.info(f"[scan] 匹配到文件: {fname} (在 {item['path']})")
        except Exception as e:
            logger.error(f"[scan] 扫描失败: {e}")
        
        logger.info(f"[scan] 最终文件: {files}, 目录: {save_dir}")
        return files, save_dir
    def _autosave_sync(self, surl: str, pwd: str) -> dict:
        """同步版本的转存，参考 media_save 插件的实现"""
        try:
            s = cffi_requests.Session(impersonate="chrome120")
            
            # 1. 登录 baidu-autosave，提取 session cookie
            login_resp = s.post(
                f"{self.autosave_url}/api/auth/login",
                json={"username": self.autosave_user, "password": self.autosave_pass},
                allow_redirects=False,
                timeout=15
            )
            login_data = login_resp.json()
            logger.info(f"[autosave] 登录响应: {login_data}")
            
            if not login_data.get("success"):
                return {"success": False, "error": "baidu-autosave 登录失败: " + login_data.get("message", "")}
            
            # 提取 session cookie
            session_val = ""
            set_cookie = login_resp.headers.get("Set-Cookie", "")
            if "session=" in set_cookie:
                session_val = set_cookie.split("session=")[1].split(";")[0]
            logger.info(f"[autosave] session: {session_val[:20]}...")
            
            headers = {"Cookie": f"session={session_val}"}
            
            # 2. 记录当前任务数量
            pre_tasks_resp = s.get(f"{self.autosave_url}/api/tasks", headers=headers, allow_redirects=False, timeout=15)
            pre_tasks_count = len(pre_tasks_resp.json().get("tasks", []))
            
            # 3. 添加任务（带 cookie，不传 name 避免被当成目录名）
            share_url = f"https://pan.baidu.com/s/1{surl}"
            
            save_resp = s.post(
                f"{self.autosave_url}/api/task/add",
                json={
                    "url": share_url,
                    "pwd": pwd or "",
                    "save_dir": self.autosave_dir
                },
                headers=headers,
                allow_redirects=False,
                timeout=30
            )
            save_data = save_resp.json()
            logger.info(f"[autosave] 添加任务响应: {save_data}")
            
            if not save_data.get("success"):
                return {"success": False, "error": save_data.get("message", "添加任务失败")}
            
            # 4. 获取任务列表，找到新增的任务
            time.sleep(1)
            
            tasks_resp = s.get(f"{self.autosave_url}/api/tasks", headers=headers, allow_redirects=False, timeout=15)
            tasks_data = tasks_resp.json()
            tasks_list = tasks_data.get("tasks", [])
            
            logger.info(f"[autosave] 任务列表: {len(tasks_list)} 个，之前: {pre_tasks_count} 个")
            
            task_uid = None
            # 找新增的任务（任务数量增加，取最后一个）
            if len(tasks_list) > pre_tasks_count:
                task_uid = tasks_list[-1].get("task_uid")
                logger.info(f"[autosave] 找到新增任务: {task_uid[:8]}..., url={tasks_list[-1].get('url','')[-30:]}")
            else:
                # 如果数量没变，找匹配 surl 的最后一个任务
                for task in reversed(tasks_list):
                    if surl in task.get("url", ""):
                        task_uid = task.get("task_uid")
                        logger.info(f"[autosave] 找到匹配任务: {task_uid[:8]}..., url={task.get('url','')[-30:]}")
                        break
            
            if not task_uid:
                return {"success": False, "error": "找不到任务"}
            
            # 4. 执行任务
            logger.info(f"[autosave] 执行任务: {task_uid}")
            exec_resp = s.post(
                f"{self.autosave_url}/api/task/execute",
                json={"task_uid": task_uid},
                headers=headers,
                allow_redirects=False,
                timeout=60
            )
            exec_data = exec_resp.json()
            logger.info(f"[autosave] 执行响应: {exec_data}")
            
            # 5. 等待执行完成
            time.sleep(8)
            
            # 6. 获取执行结果（直接按 task_uid 查找，不管状态）
            result_resp = s.get(f"{self.autosave_url}/api/tasks", headers=headers, allow_redirects=False, timeout=15)
            result_data = result_resp.json()
            
            transferred_files = []
            save_dir = self.autosave_dir
            logger.info(f"[autosave] 查找任务结果: task_uid={task_uid[:8]}..., 任务列表: {len(result_data.get('tasks', []))} 个")
            for task in result_data.get("tasks", []):
                if task.get("task_uid") == task_uid:
                    msg = task.get("message", "")
                    files = task.get("transferred_files", [])
                    logger.info(f"[autosave] 任务结果: msg={msg}, files={files}")
                    
                    # 只要有文件就算成功（某些文件失败不影响整体）
                    if files:
                        transferred_files = files
                        first_file = files[0]
                        parts = first_file.strip("/").split("/")
                        if len(parts) > 1:
                            save_dir = "/" + parts[0]
                    elif "没有新文件" in msg or "跳过" in msg:
                        logger.info(f"[autosave] 文件已存在，跳过转存")
                        # 清理任务
                        try:
                            s.post(
                                f"{self.autosave_url}/api/task/delete",
                                json={"task_id": 0},
                                headers=headers,
                                allow_redirects=False,
                                timeout=15
                            )
                        except Exception:
                            pass
                        return {"success": True, "files": [], "save_dir": self.autosave_dir, "existed": True}
                    elif "失败" in msg or "错误" in msg:
                        return {"success": False, "error": msg}
                    break
            
            logger.info(f"[autosave] 任务完成，将从百度网盘扫描实际文件")
            
            # 7. 清理任务
            try:
                s.post(
                    f"{self.autosave_url}/api/task/delete",
                    json={"task_id": 0},
                    headers=headers,
                    allow_redirects=False,
                    timeout=15
                )
            except Exception:
                pass
            
            # 返回成功，files 里保存文件名（用于后续匹配）
            file_names = [f.split("/")[-1] if "/" in f else f for f in transferred_files]
            return {"success": True, "files": file_names, "save_dir": self.autosave_dir}
        except Exception as e:
            logger.error(f"[autosave] 转存失败: {e}")
            return {"success": False, "error": str(e)}
    async def _get_dlinks(self, save_dir: str, file_names: list = None) -> list:
        loop = asyncio.get_running_loop()
        
        return await loop.run_in_executor(None, self._get_dlinks_sync, save_dir, file_names)

    # ---- 移动文件夹 ----

    
    async def _move_folder(self, from_dir: str, to_dir: str) -> bool:
        """用 OpenList API 移动整个文件夹"""
        if not self.openlist_url or not self.openlist_user:
            logger.warning("[move] 未配置 OpenList，跳过移动")
            return False
        
        try:
            
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, 
                self._move_folder_sync, from_dir, to_dir)
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
                timeout=15
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
                json={"path": f"{pan_prefix}/", "page": 1, "per_page": 100, "refresh": True},
                timeout=aiohttp.ClientTimeout(total=10)
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
                    sharelink_dirs.append({
                        "name": name,
                        "modified": item.get("modified", 0)
                    })
            
            if not sharelink_dirs:
                logger.info("[move] 没有找到 sharelink 文件夹，文件可能已在目标目录")
                return True
            
            # 按修改时间排序，找最近的
            sharelink_dirs.sort(key=lambda x: x["modified"], reverse=True)
            newest_dir = sharelink_dirs[0]
            
            logger.info(f"[move] 找到最新 sharelink 文件夹: {newest_dir['name']} (修改时间: {newest_dir['modified']})")
            
            # 移动这个文件夹到目标目录
            src_path = f"{pan_prefix}/{newest_dir['name']}"
            dst_path = f"{pan_prefix}{to_dir}/{newest_dir['name']}"
            
            logger.info(f"[move] 移动: {src_path} -> {dst_path}")
            
            # 获取源目录里的所有文件
            resp2 = s.post(
                f"{self.openlist_url}/api/fs/list",
                headers=headers,
                json={"path": src_path, "page": 1, "per_page": 100, "refresh": True},
                timeout=aiohttp.ClientTimeout(total=10)
            )
            data2 = resp2.json()
            
            files = [f.get("name") for f in (data2.get("data", {}).get("content") or [])]
            
            if not files:
                logger.warning("[move] 源目录为空")
                return False
            
            logger.info(f"[move] 源目录文件: {files}")
            
            # 创建目标子目录
            s.post(
                f"{self.openlist_url}/api/fs/mkdir",
                headers=headers,
                json={"path": dst_path},
                timeout=aiohttp.ClientTimeout(total=10)
            )
            
            # 移动所有文件
            move_resp = s.post(
                f"{self.openlist_url}/api/fs/move",
                headers=headers,
                json={
                    "src_dir": src_path,
                    "dst_dir": dst_path,
                    "names": files
                },
                timeout=aiohttp.ClientTimeout(total=30)
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
                    timeout=aiohttp.ClientTimeout(total=10)
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
                    timeout=aiohttp.ClientTimeout(total=10)
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
                timeout=15
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
                json={
                    "src_dir": src_dir,
                    "dst_dir": dst_dir,
                    "names": names
                },
                timeout=aiohttp.ClientTimeout(total=30)
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
    
    # ---- 清理 baidu-autosave 任务 ----

    
    async def _cleanup_autosave_task(self, surl: str):
        """删除 baidu-autosave 里的任务"""
        if not self.autosave_url:
            return
        
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._cleanup_sync, surl)
        except Exception as e:
            logger.warning(f"[cleanup] 清理任务失败: {e}")
    
    def _cleanup_sync(self, surl: str):
        """同步清理任务（在线程池中执行）"""
        try:
            s = cffi_requests.Session(impersonate="chrome120")
            
            # 登录
            login_resp = s.post(
                f"{self.autosave_url}/api/auth/login",
                json={"username": self.autosave_user, "password": self.autosave_pass},
                allow_redirects=False,
                timeout=15
            )
            
            session_val = ""
            set_cookie = login_resp.headers.get("Set-Cookie", "")
            if "session=" in set_cookie:
                session_val = set_cookie.split("session=")[1].split(";")[0]
            headers = {"Cookie": f"session={session_val}"}
            
            # 获取任务列表
            tasks_resp = s.get(f"{self.autosave_url}/api/tasks", headers=headers, allow_redirects=False, timeout=15)
            tasks = tasks_resp.json().get("tasks", [])
            
            # 找到匹配的任务
            task_uids = []
            for task in tasks:
                if surl in task.get("url", ""):
                    task_uids.append(task.get("task_uid"))
            
            # 删除匹配的任务（用 task_id，从后往前删避免 ID 变化）
            if task_uids:
                # 获取最新任务列表找到对应的 task_id
                tasks_resp2 = s.get(f"{self.autosave_url}/api/tasks", headers=headers, allow_redirects=False, timeout=15)
                tasks_list = tasks_resp2.json().get("tasks", [])
                # 收集要删除的 task_id，然后从后往前删除
                ids_to_delete = []
                for i, task in enumerate(tasks_list):
                    if task.get("task_uid") in task_uids:
                        ids_to_delete.append(i)
                # 从后往前删除
                for task_id in reversed(ids_to_delete):
                    s.post(
                        f"{self.autosave_url}/api/task/delete",
                        json={"task_id": task_id},
                        headers=headers,
                        allow_redirects=False,
                        timeout=15
                    )
                logger.info(f"[cleanup] 已删除 {len(ids_to_delete)} 个任务")
                
        except Exception as e:
            logger.warning(f"[cleanup] 清理任务失败: {e}")
