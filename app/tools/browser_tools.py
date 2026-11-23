"""Видимый браузер (Playwright, headless=False): пользователь ВИДИТ курсор,
клики и поиск. Каждое действие возвращает наблюдение + скриншот для интерфейса.

Все вызовы выполняются в одном выделенном потоке (требование sync-API Playwright).
"""
import base64
import queue
import threading
from concurrent.futures import Future
from urllib.parse import quote_plus

from .. import config

SEARCH_URL = "https://duckduckgo.com/html/?q="


class BrowserManager:
    def __init__(self):
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.error: str | None = None
        self.started = False

    # --- инфраструктура потока ------------------------------------------------
    def _ensure_thread(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="browser-worker")
        self._thread.start()

    def _loop(self):
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:                                   # pragma: no cover
            self.error = (f"Playwright не установлен: {e}. "
                          "Выполни: pip install playwright && playwright install chromium")
            self.started = True
            while True:
                item = self._q.get()
                if item is None:
                    return
                _, _, fut = item
                fut.set_result({"obs": "⛔ " + self.error, "shot": None})
            return
        try:
            with sync_playwright() as p:
                self.browser = p.chromium.launch(
                    headless=False, args=["--start-maximized", "--lang=ru"])
                self.context = self.browser.new_context(
                    viewport={"width": 1200, "height": 720}, locale="ru-RU")
                self.page = self.context.new_page()
                self.page.set_default_timeout(20000)
                self.started = True
                while True:
                    item = self._q.get()
                    if item is None:
                        break
                    fn, kwargs, fut = item
                    try:
                        fut.set_result(fn(**kwargs))
                    except Exception as e:
                        fut.set_result({"obs": f"⚠️ Ошибка браузера: {e}",
                                        "shot": self._shot()})
                try:
                    self.browser.close()
                except Exception:
                    pass
        except Exception as e:                                   # pragma: no cover
            self.error = f"Браузер не запустился: {e}"
            self.started = True

    def call(self, fn, **kwargs) -> Future:
        if config.DEMO_MODE:
            fut: Future = Future()
            fut.set_result(_demo_action(fn.__name__, kwargs))
            return fut
        self._ensure_thread()
        fut = Future()
        self._q.put((fn, kwargs, fut))
        return fut

    def stop(self):
        if not config.DEMO_MODE:
            self._q.put(None)

    # --- действия (выполняются внутри потока браузера) -------------------------
    def _shot(self) -> str | None:
        try:
            return base64.b64encode(self.page.screenshot(type="png")).decode()
        except Exception:
            return None

    def _loc(self, target: str):
        t = (target or "").strip()
        low = t.lower()
        # лечим типовые ошибки моделей: css:: / css= / text= / дубли префиксов
        if low.startswith("css:"):
            rest = t[4:]
            while rest.startswith(":"):
                rest = rest[1:]
            return self.page.locator(rest).first
        if low.startswith("css="):
            return self.page.locator(t[4:].strip()).first
        if low.startswith("text:"):
            return self.page.get_by_text(t[5:].strip()).first
        if low.startswith("text="):
            return self.page.get_by_text(t[5:].strip()).first
        if low.startswith("ph:"):
            return self.page.get_by_placeholder(t[3:].strip()).first
        return self.page.locator(t).first

    @staticmethod
    def _bad_target(e: Exception, target: str) -> dict:
        return {"obs": (f"⚠ Не вышло с целью «{target}»: {str(e).splitlines()[0][:160]}. "
                        "Форматы цели: «text:Текст кнопки», «css=#id», «css=.class», "
                        "«ph:Плейсхолдер». Выбери другую цель, прочитай страницу "
                        "(browser_text) или верни final с собранной информацией."),
                "shot": None}

    def search(self, query: str) -> dict:
        self.page.goto(SEARCH_URL + quote_plus(query))
        self.page.wait_for_load_state("domcontentloaded")
        items = []
        for res in self.page.locator(".result").all()[:5]:
            try:
                a = res.locator(".result__a").first
                title = a.inner_text().strip()
                href = a.get_attribute("href") or ""
                snip = res.locator(".result__snippet").first.inner_text().strip()
                items.append(f"• {title}\n  {snip[:220]}\n  {href}")
            except Exception:
                continue
        obs = (f"Поиск «{query}»: найдено результатов {len(items)}.\n"
               + "\n".join(items)) if items else \
              f"Поиск «{query}»: страница открыта, но элементы результатов не распознаны."
        return {"obs": obs, "shot": self._shot()}

    def open_url(self, url: str) -> dict:
        self.page.goto(url if "://" in url else "https://" + url)
        self.page.wait_for_load_state("domcontentloaded")
        title = self.page.title()
        return {"obs": f"Открыта страница: {title} ({self.page.url})",
                "shot": self._shot()}

    def click(self, target: str) -> dict:
        try:
            loc = self._loc(target)
            try:  # подсветка клика — пользователь видит, КУДА нажали
                loc.evaluate("e=>{e.style.outline='3px solid red';"
                             "setTimeout(()=>e.style.outline='',1500);}")
            except Exception:
                pass
            loc.click()
            self.page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            return self._bad_target(e, target)
        return {"obs": f"Клик по: {target} (страница: {self.page.title()})",
                "shot": self._shot()}

    def type_text(self, target: str, text: str, submit: bool = False) -> dict:
        try:
            loc = self._loc(target)
            loc.fill(text)
            if submit:
                loc.press("Enter")
                self.page.wait_for_load_state("domcontentloaded")
        except Exception as e:
            return self._bad_target(e, target)
        return {"obs": f"Введено «{text[:60]}» в {target}"
                       + (" + Enter" if submit else ""),
                "shot": self._shot()}

    def page_text(self) -> dict:
        txt = self.page.inner_text("body")[:4000]
        return {"obs": f"Текст страницы {self.page.title()}:\n{txt}",
                "shot": self._shot()}

    def tabs(self) -> dict:
        pages = self.context.pages
        lines = [f"{i}: {p.title()} — {p.url}" for i, p in enumerate(pages)]
        return {"obs": "Вкладки:\n" + "\n".join(lines), "shot": self._shot()}

    def close_tab(self, index: int = -1) -> dict:
        pages = self.context.pages
        if not pages:
            return {"obs": "Вкладок нет", "shot": None}
        i = index if 0 <= index < len(pages) else len(pages) - 1
        pages[i].close()
        rest = self.context.pages
        if rest:
            self.page = rest[-1]
        return {"obs": f"Закрыта вкладка {i}; осталось {len(rest)}",
                "shot": self._shot()}

    def new_tab(self, url: str = "about:blank") -> dict:
        self.page = self.context.new_page()
        if url != "about:blank":
            self.page.goto(url)
        return {"obs": f"Открыта новая вкладка: {url}", "shot": self._shot()}


def _demo_action(name: str, kwargs: dict) -> dict:
    """Заглушки браузера для демо-режима (без Playwright)."""
    q = kwargs.get("query") or kwargs.get("target") or kwargs.get("url") or ""
    obs = {
        "search": f"Поиск «{q}» (демо): 1) локальные модели приватны; "
                  f"2) Ollama держит модели в RAM; 3) RAG = память по смыслу.",
        "open_url": f"(демо) Открыта страница {q}",
        "click": f"(демо) Клик по {q}",
        "type_text": f"(демо) Ввод текста в {q}",
        "page_text": "(демо) Текст страницы: пример содержимого.",
        "tabs": "(демо) Вкладки: 0: Пример — about:blank",
        "close_tab": "(демо) Вкладка закрыта",
        "new_tab": "(демо) Новая вкладка открыта",
    }.get(name, f"(демо) действие {name}")
    return {"obs": obs, "shot": None}


manager = BrowserManager()
