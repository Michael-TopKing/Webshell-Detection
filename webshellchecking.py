#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import sys
import time
from collections import defaultdict, deque, Counter
from typing import List, Dict
from urllib.parse import urlparse
import aiohttp
from colorama import Fore, Style, init


# ====================== 配置区 ======================
CRITICAL_FINGERPRINTS = {"wso", "filesman", "b374k", "c99", "r57", "sym", "indoxploit", "madspot", "priv8"}

CRITICAL_REGEX = [
    r"system\s*\(", r"exec\s*\(", r"passthru\s*\(", r"shell_exec\s*\(",
    r"eval\s*\(", r"assert\s*\(", r"base64_decode\s*\(", r"gzinflate\s*\("
]

ALLOWED_CONTENT_TYPES = {'text/html', 'text/plain', 'application/xhtml+xml'}
MAX_RESPONSE_SIZE = 2_000_000

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]


# ====================== 主类定义 ======================
class WebshellDetector:
    def __init__(self, args):
        self.args = args
        self.setup_logging()
        self.session = None
        
        # ====================== 并发控制 ======================
        self.global_semaphore = asyncio.Semaphore(args.global_limit)
        self.host_semaphores = defaultdict(lambda: asyncio.Semaphore(4))
        
        # ====================== 错误页过滤机制（增强版） ======================
        self.error_page_hashes = defaultdict(lambda: deque(maxlen=180))
        self.error_page_lengths = defaultdict(lambda: deque(maxlen=50))   # 新增：长度相似过滤
        
        self.compiled_regex = [re.compile(p, re.IGNORECASE) for p in CRITICAL_REGEX]
        self.title_re = re.compile(r'<title>(.*?)</title>', re.I | re.S)
        
        self.seen_urls = set()
        self.seen_lock = asyncio.Lock()
        
        # ====================== 统计信息 ======================
        self.total_checked = 0
        self.total_found = 0
        self.start_time = time.time()
        
        # ====================== 输出文件 ======================
        self.result_file = open(self.args.output, 'w', encoding='utf-8')
        self.url_only_file = open("found_webshells.txt", 'w', encoding='utf-8')
        self.json_file = open("findings.jsonl", 'w', encoding='utf-8') if args.json else None
        
        self.files = {
            "CRITICAL": open("critical.txt", 'w', encoding='utf-8'),
            "HIGH": open("high.txt", 'w', encoding='utf-8'),
            "SUSPICIOUS": open("suspicious.txt", 'w', encoding='utf-8'),
        }
        
        self.print_lock = asyncio.Lock()
        self.file_lock = asyncio.Lock()
        self.stats = Counter()
        self.last_adjust_time = time.time()
        self.current_global_limit = args.global_limit
        self.min_global_limit = max(10, args.global_limit // 6)  # 降速下限，避免降到 0 卡死
        self._waf_logged_hosts = set()  # 避免同一 host 的 WAF 日志刷屏


    # ====================== 日志设置 ======================
    def setup_logging(self):
        level = logging.DEBUG if getattr(self.args, 'verbose', False) else logging.INFO
        logging.basicConfig(level=level,
                           format='%(asctime)s | %(levelname)s | %(message)s',
                           handlers=[logging.FileHandler('webshell_detector.log', encoding='utf-8')])
        self.logger = logging.getLogger(__name__)


    # ====================== HTTP 会话初始化 ======================
    async def init_session(self):
        connector = aiohttp.TCPConnector(limit=600, ttl_dns_cache=300, keepalive_timeout=35, ssl=False)
        timeout = aiohttp.ClientTimeout(total=22, connect=12, sock_read=18)
        self.session = aiohttp.ClientSession(timeout=timeout, connector=connector)


    def get_random_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": random.choice(["en-US,en;q=0.9", "zh-CN,zh;q=0.9,en;q=0.8"]),
            "Connection": "keep-alive",
        }


    def safe_url(self, base: str, filename: str) -> str:
        return f"{base.rstrip('/')}/{filename.lstrip('/')}"


    # ====================== 实时状态监控 ======================
    async def stats_monitor(self):
        while True:
            elapsed = time.time() - self.start_time
            speed = self.total_checked / elapsed if elapsed > 0 else 0
            timestamp = time.strftime("%H:%M:%S")
            
            async with self.print_lock:
                line = f"\r{Fore.CYAN}[{timestamp}] Tested: {self.total_checked:,} | Found: {self.total_found} | Speed: {speed:.1f} req/s{Style.RESET_ALL}"
                sys.stdout.write(line)
                sys.stdout.flush()
            await asyncio.sleep(1)


    # ====================== 核心检测函数 ======================
    async def check_url(self, base_url: str, filename: str):
        full_url = self.safe_url(base_url, filename)
        
        # 去重
        async with self.seen_lock:
            if full_url in self.seen_urls:
                return
            self.seen_urls.add(full_url)
        
        host = urlparse(full_url).netloc
        
        # ====================== 并发控制 ======================
        async with self.global_semaphore, self.host_semaphores[host]:
            for attempt in range(3):
                try:
                    async with self.session.get(full_url, headers=self.get_random_headers(),
                                              allow_redirects=self.args.allow_redirect) as resp:
                        
                        self.total_checked += 1
                        self.stats[resp.status] += 1
                        
                        ct = resp.headers.get('Content-Type', '').lower()
                        if not any(allowed in ct for allowed in ALLOWED_CONTENT_TYPES):
                            return
                        
                        body = await resp.content.read(MAX_RESPONSE_SIZE + 8192)
                        if len(body) > MAX_RESPONSE_SIZE:
                            return
                            
                        content = body.decode('utf-8', errors='ignore')
                        
                        # 低信息页面过滤
                        if len(content) < 280 or (content.count("\n") < 3 and len(content) < 500):
                            return
                        
                        # WAF / 封锁检测
                        if self.is_waf_or_blocked(resp.status, content):
                            if host not in self._waf_logged_hosts:
                                self._waf_logged_hosts.add(host)
                                self.logger.warning(f"WAF/拦截检测触发 host={host} status={resp.status} url={full_url}")
                            self._adaptive_adjust()
                            return
                        
                        # 错误页过滤（增强版）
                        if not self.args.disable_error_filter:
                            if self.is_likely_error_page(host, content):
                                return
                        
                        title_match = self.title_re.search(content)
                        title = title_match.group(1).strip() if title_match else ""
                        
                        # 风险计算
                        score, risk, matched = self.calculate_risk(content, title)
                        
                        # 最终检测条件加强
                        if (score >= self.args.min_score and 
                            len(matched) >= 2 and 
                            len(content) > 300):
                            await self.save_result(full_url, score, risk, matched, title)
                            self.total_found += 1
                    
                    self._adaptive_adjust()
                    break
                    
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    self.stats['timeout'] += 1
                    self._adaptive_adjust()
                    if attempt < 2:
                        await asyncio.sleep(random.uniform(0.8, 2.5))
                        continue
                    self.logger.debug(f"超时/连接失败，重试耗尽: {full_url} ({type(e).__name__})")
                except Exception as e:
                    self.logger.debug(f"未预期异常，放弃该URL: {full_url} ({type(e).__name__}: {e})")
                    break


    # ====================== 保存结果（增加 flush） ======================
    async def save_result(self, url: str, score: int, risk: str, matched: list, title: str):
        timestamp = time.strftime("%H:%M:%S")
        
        async with self.file_lock:
            self.result_file.write(f"[{risk}] {score} {url}\n")
            self.url_only_file.write(url + "\n")
            
            if self.json_file:
                json_line = json.dumps({
                    "url": url, "score": score, "risk": risk,
                    "matched": matched, "title": title[:200], "time": timestamp
                }, ensure_ascii=False)
                self.json_file.write(json_line + "\n")
            
            if risk in self.files:
                self.files[risk].write(url + "\n")
            
            # 关键改进：立即 flush，防止数据丢失
            self.result_file.flush()
            self.url_only_file.flush()
            if self.json_file:
                self.json_file.flush()
            for f in self.files.values():
                f.flush()
        
        async with self.print_lock:
            color = Fore.RED if risk == "CRITICAL" else Fore.YELLOW if risk == "HIGH" else Fore.CYAN
            print(f"\r{color}[{timestamp}] [{risk}] {score} → {url}{Style.RESET_ALL}")

        self.logger.info(f"FOUND [{risk}] score={score} url={url} matched={matched} title={title[:100]!r}")


    # ====================== 风险评分（增强组合检测） ======================
    def calculate_risk(self, content: str, title: str):
        text_lower = content.lower()
        title_lower = title.lower() if title else ""
        score = 0
        matched = []
        
        # 指纹匹配
        fp_count = sum(1 for fp in CRITICAL_FINGERPRINTS if fp in text_lower or fp in title_lower)
        if fp_count >= 1:
            score += 25 * fp_count
            matched.append(f"FINGERPRINT×{fp_count}")
        
        # 正则匹配
        php_context = bool(re.search(r'<\?php|<\?', content[:800]))
        critical_count = sum(1 for pattern in self.compiled_regex if pattern.search(content))
        score += critical_count * 28
        if critical_count >= 1:
            matched.append(f"REGEX×{critical_count}")
        
        # 新增：高危组合检测
        if "eval(" in text_lower and "base64_decode(" in text_lower:
            score += 40
            matched.append("EVAL_BASE64")
        
        if "gzinflate(" in text_lower and "base64_decode(" in text_lower:
            score += 35
            matched.append("OBFUSCATED")
        
        if critical_count >= 2 and php_context:
            score += 35
            matched.append("MULTI_EXEC_PHP")
        
        # 其他危险特征
        if "<textarea" in text_lower and any(k in text_lower for k in ["cmd", "exec", "shell", "system"]):
            score += 25
            matched.append("TEXTAREA_CMD")
        
        if any(x in text_lower for x in ['type="file"', 'upload file', 'file manager', 'uploader']):
            score += 22
            matched.append("UPLOAD_UI")
        
        # 误报降权
        if any(word in text_lower for word in ["tutorial", "example", "demo", "documentation", "blog", "article", "github"]):
            score -= 25
        
        final_score = min(max(int(score), 0), 100)
        
        if final_score >= 80: risk = "CRITICAL"
        elif final_score >= 65: risk = "HIGH"
        elif final_score >= self.args.min_score: risk = "SUSPICIOUS"
        else: risk = "LOW"
        
        return final_score, risk, matched


    def is_waf_or_blocked(self, status: int, content: str) -> bool:
        if status not in {200, 301, 302}:
            return True
        text = content.lower()[:1500]
        signs = ["cloudflare", "cf-ray", "captcha", "sucuri", "attention required"]
        return any(sign in text for sign in signs)


    # ====================== 错误页检测（双重过滤：Hash + 长度） ======================
    def is_likely_error_page(self, host: str, content: str) -> bool:
        if len(content) < 280:
            return True
        
        length = len(content)
        short_hash = hashlib.md5(content[:2600].encode()).hexdigest()
        
        # Hash 过滤
        self.error_page_hashes[host].append(short_hash)
        if self.error_page_hashes[host].count(short_hash) >= 5:        # 降低阈值
            return True
        
        # 新增：内容长度相似度过滤
        self.error_page_lengths[host].append(length)
        similar = sum(1 for x in self.error_page_lengths[host] if abs(x - length) < 30)
        if similar >= 5:
            return True
        
        return False


    # ====================== 自适应限速（加速 + 降速双向） ======================
    def _adaptive_adjust(self):
        now = time.time()
        if now - self.last_adjust_time < 8:      # 每8秒检查一次
            return
        
        total = sum(self.stats.values())
        if total < 200:                           
            return
        
        # 健康请求（200/301/302）与超时/异常分别统计
        rate_ok = (self.stats.get(200, 0) + self.stats.get(301, 0) + self.stats.get(302, 0)) / total
        rate_timeout = self.stats.get('timeout', 0) / total
        
        old_limit = self.current_global_limit
        
        # ---- 降速：错误率高或超时多，先保护目标站点/自身连接池 ----
        if (rate_ok < 0.5 or rate_timeout > 0.15) and self.current_global_limit > self.min_global_limit:
            # 错误越严重，降得越猛（最多降 50%）
            severity = max(rate_timeout, 1 - rate_ok)
            cut_ratio = min(0.5, 0.2 + severity * 0.3)
            self.current_global_limit = max(
                self.min_global_limit,
                int(self.current_global_limit * (1 - cut_ratio))
            )
            self.global_semaphore = asyncio.Semaphore(self.current_global_limit)
            self.logger.warning(
                f"Adaptive DOWN {old_limit} → {self.current_global_limit} "
                f"(rate_ok={rate_ok:.2f}, rate_timeout={rate_timeout:.2f})"
            )
        
        # ---- 加速：大部分请求都成功，且没有明显超时压力 ----
        elif rate_ok > 0.75 and rate_timeout < 0.05 and self.current_global_limit < self.args.global_limit:
            self.current_global_limit = min(self.args.global_limit, self.current_global_limit + 15)
            self.global_semaphore = asyncio.Semaphore(self.current_global_limit)
            self.logger.info(
                f"Adaptive UP {old_limit} → {self.current_global_limit} "
                f"(rate_ok={rate_ok:.2f}, rate_timeout={rate_timeout:.2f})"
            )
        
        self.last_adjust_time = now
        self.stats.clear()                        # 清空统计，准备下一轮


    # ====================== 任务生产者 ======================
    async def producer(self, queue: asyncio.Queue, directories: List[str], filenames: List[str]):
        dirs = directories[:]
        files = filenames[:]
        random.shuffle(dirs)
        random.shuffle(files)
        for d in dirs:
            random.shuffle(files)
            for f in files:
                await queue.put((d, f))


    async def worker(self, queue: asyncio.Queue):
        while True:
            item = None
            try:
                item = await queue.get()
                await self.check_url(*item)
            except asyncio.CancelledError:
                break
            finally:
                if item is not None:
                    queue.task_done()


    # ====================== 主运行流程 ======================
    async def run(self):
        await self.init_session()
        try:
            directories = self.load_file(self.args.directories)
            filenames = self.load_file(self.args.dictionary)
            total = len(directories) * len(filenames)
            
            print(f"{Fore.GREEN}[+] Scan started → {total:,} targets{Style.RESET_ALL}")
            self.logger.info(
                f"扫描开始: dirs={len(directories)} files={len(filenames)} total_targets={total} "
                f"concurrency={self.args.concurrency} global_limit={self.args.global_limit} "
                f"min_score={self.args.min_score} output={self.args.output}"
            )
            
            queue: asyncio.Queue = asyncio.Queue(maxsize=self.args.concurrency * 150)
            monitor = asyncio.create_task(self.stats_monitor())
            
            workers = [asyncio.create_task(self.worker(queue)) for _ in range(self.args.concurrency)]
            producer = asyncio.create_task(self.producer(queue, directories, filenames))
            
            await producer
            await queue.join()
            
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            monitor.cancel()
            
            print(f"\n{Fore.GREEN}[+] Scan completed!{Style.RESET_ALL}")
            elapsed = time.time() - self.start_time
            self.logger.info(
                f"扫描完成: checked={self.total_checked} found={self.total_found} "
                f"elapsed={elapsed:.1f}s final_limit={self.current_global_limit}"
            )
            
        except asyncio.CancelledError:
            print(f"\n{Fore.YELLOW}[!] Interrupted by user{Style.RESET_ALL}")
            self.logger.warning(
                f"扫描被用户中断: checked={self.total_checked} found={self.total_found}"
            )
        finally:
            self.result_file.close()
            self.url_only_file.close()
            if self.json_file:
                self.json_file.close()
            for f in self.files.values():
                f.close()
            if self.session:
                await self.session.close()


    def load_file(self, filepath: str) -> List[str]:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]


# ====================== 程序入口 ======================
def main():
    init(autoreset=True)
    parser = argparse.ArgumentParser(description="Webshell Detector v7.8 - Optimized")
    parser.add_argument('--directories', '-d', required=True)
    parser.add_argument('--dictionary', '-w', required=True)
    parser.add_argument('-o', '--output', default='results.txt')
    parser.add_argument('--min-score', type=int, default=62)
    parser.add_argument('--concurrency', '-c', type=int, default=100)
    parser.add_argument('--global-limit', type=int, default=180)
    parser.add_argument('--disable-error-filter', action='store_true')
    parser.add_argument('--allow-redirect', action='store_true')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--verbose', action='store_true',
                         help='写入 DEBUG 级别日志（含逐条超时/异常记录），默认只记录 INFO/WARNING')
    
    args = parser.parse_args()
    detector = WebshellDetector(args)
    asyncio.run(detector.run())


if __name__ == "__main__":
    main()
