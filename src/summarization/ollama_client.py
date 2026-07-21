"""Ollama client"""

import os
import shutil
import subprocess
import sys
import threading
import requests
import json as _json
import time
from typing import Callable, List, Optional
from src.i18n import t
from src.utils.exceptions import SummarizationError
from src.utils.logger import get_logger
from src.utils.subprocess_compat import CREATE_NO_WINDOW

logger = get_logger(__name__)


class OllamaClient:
    """Ollama client — manages Ollama HTTP communication and service process lifecycle"""

    # Class-level process reference, shared across all instances
    _service_process: Optional[subprocess.Popen] = None

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout: int = 60):
        """Initialize Ollama client

        Args:
            base_url: Ollama service URL
            timeout: Request timeout (seconds)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = 3
        self._session = requests.Session()
        ollama_api_key = os.environ.get("OLLAMA_API_KEY") or ""
        if ollama_api_key:
            self._session.headers.update({"Authorization": f"Bearer {ollama_api_key}"})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP Session, releasing the connection pool."""
        self._session.close()

    # ------------------------------------------------------------------
    # Service process management
    # ------------------------------------------------------------------

    @classmethod
    def start_service(
        cls, url: str = "http://127.0.0.1:11434", quiet: bool = False
    ) -> bool:
        """Start the Ollama service process (if not already running).

        If Ollama was started externally (e.g. the user ran ``ollama serve`` manually),
        this method will detect it via HTTP probe and return ``True`` without
        recording the process reference — subsequent calls to ``stop_service()``
        will not terminate the external process.

        Args:
            url: Ollama service URL
            quiet: If True, suppress logs (caller manages logging)

        Returns:
            Whether the service is ready (already running or successfully started)
        """
        # Already running? (possibly started externally, not managed by this client)
        try:
            resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2)
            if resp.status_code == 200:
                if not quiet:
                    logger.info(t("services.summarization.ollama.already_running"))
                return True
        except Exception:
            pass

        # Already started by this client and still alive?
        if cls._service_process is not None and cls._service_process.poll() is None:
            if not quiet:
                logger.info(t("services.summarization.ollama.process_exists"))
            return True

        ollama_path = shutil.which("ollama")
        if not ollama_path:
            if not quiet:
                logger.error(t("services.summarization.ollama.cmd_not_found"))
            return False

        try:
            if not quiet:
                logger.info(t("services.summarization.ollama.starting"))
            cls._service_process = subprocess.Popen(
                [ollama_path, "serve"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ},
            )
            if not quiet:
                logger.info(
                    t("services.summarization.ollama.start_cmd_executed", pid=cls._service_process.pid)
                )

            for _ in range(10):
                time.sleep(0.5)
                if cls._service_process.poll() is not None:
                    if not quiet:
                        logger.error(
                            t("services.summarization.ollama.process_exited", code=cls._service_process.returncode),
                        )
                    cls._service_process = None
                    return False
                try:
                    resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2)
                    if resp.status_code == 200:
                        if not quiet:
                            logger.info(t("services.summarization.ollama.service_ready"))
                        return True
                except requests.RequestException:
                    pass

            if not quiet:
                logger.warning(t("services.summarization.ollama.start_no_response"))
            return True
        except Exception as e:
            if not quiet:
                logger.error(t("services.summarization.ollama.start_failed", error=e))
            cls._service_process = None
            return False

    @classmethod
    def stop_service(cls) -> None:
        """Stop the Ollama service process (with all child processes) started by this client."""
        proc = cls._service_process
        if proc is None:
            return
        cls._service_process = None
        if proc.poll() is not None:
            return
        pid = proc.pid
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
        except Exception:
            pass
        logger.info(t("services.summarization.ollama.stopped", pid=pid))

    @classmethod
    def is_service_running(cls, url: str = "http://127.0.0.1:11434") -> bool:
        """Check if the Ollama service is running (via HTTP probe).

        Args:
            url: Ollama service URL

        Returns:
            Whether the service is reachable
        """
        try:
            resp = requests.get(f"{url.rstrip('/')}/api/tags", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    @classmethod
    def ensure_service(
        cls,
        url: str = "http://127.0.0.1:11434",
        max_retries: int = 3,
        wait_seconds: float = 5,
    ) -> bool:
        """Ensure Ollama service is available: auto-start if not running, retry up to max_retries.

        Args:
            url: Ollama service URL
            max_retries: Maximum number of retries
            wait_seconds: Seconds to wait between retries

        Returns:
            Whether the service is finally available

        Raises:
            RuntimeError: If service is still unavailable after all retries
        """
        if cls.is_service_running(url):
            logger.info("Ollama " + t("services.summarization.ollama.status_running"))
            return True

        logger.info("Ollama " + t("services.summarization.ollama.status_not_running"))

        if not cls.start_service(url, quiet=True):
            logger.info("  └─ " + t("services.summarization.ollama.start_executed") + " ✗ " + t("services.summarization.ollama.cmd_not_found"))
            raise RuntimeError(t("services.summarization.ollama.service_cmd_not_found"))

        logger.info("  ├─ " + t("services.summarization.ollama.start_executed"))

        for attempt in range(1, max_retries + 1):
            logger.info(
                "  ├─ " + t("services.summarization.ollama.waiting", attempt=attempt, max=max_retries, seconds=wait_seconds),
            )
            time.sleep(wait_seconds)
            if cls.is_service_running(url):
                logger.info("  └─ " + t("services.summarization.ollama.ready_ok", attempt=attempt))
                return True

        logger.info("  └─ " + t("services.summarization.ollama.ready_fail", count=max_retries))
        raise RuntimeError(
            t("services.summarization.ollama.service_unreachable", count=max_retries, url=url)
        )

    @classmethod
    def full_check(
        cls,
        url: str = "http://127.0.0.1:11434",
        model_name: str = "",
        max_retries: int = 3,
        wait_seconds: float = 5,
    ) -> bool:
        """All-in-one check: start service → connection test → list models → verify model.

        All logs are grouped under the ``Ollama `` prefix.

        Args:
            url: Ollama service URL
            model_name: Model name to verify (skip if empty)
            max_retries: Startup retry count
            wait_seconds: Seconds to wait between retries

        Returns:
            Whether the service is usable (connected and model exists, if specified)
        """
        logger.info("Ollama")

        # ── 1. Ensure service is running ──────────────────────────
        if cls.is_service_running(url):
            logger.info("  ├─ " + t("services.summarization.ollama.status_running"))
        else:
            logger.info("  ├─ " + t("services.summarization.ollama.status_not_running"))
            if not cls.start_service(url, quiet=True):
                logger.info("  └─ " + t("services.summarization.ollama.cmd_not_found"))
                return False
            logger.info("  ├─ " + t("services.summarization.ollama.start_executed"))
            ready = False
            for attempt in range(1, max_retries + 1):
                logger.info(
                    "  ├─ " + t("services.summarization.ollama.waiting", attempt=attempt, max=max_retries, seconds=wait_seconds),
                )
                time.sleep(wait_seconds)
                if cls.is_service_running(url):
                    logger.info("  ├─ " + t("services.summarization.ollama.ready_ok", attempt=attempt))
                    ready = True
                    break
            if not ready:
                logger.info("  └─ " + t("services.summarization.ollama.ready_fail", count=max_retries))
                return False

        # ── 2. Connection check ──────────────────────────────────
        client = cls(url)
        try:
            ok = client.check_connection(quiet=True)
            if not ok:
                logger.info("  └─ " + t("services.summarization.ollama.connection_fail"))
                return False

            # ── 3. List available models ─────────────────────────
            models = client.list_models(quiet=True)
            has_model_check = bool(model_name)

            if models:
                logger.info("  ├─ " + t("services.summarization.ollama.connection_ok"))
                if has_model_check:
                    logger.info("  ├─ " + t("services.summarization.ollama.available_models"))
                    for i, name in enumerate(models):
                        branch = "├─" if i < len(models) - 1 else "└─"
                        logger.info("  │  %s %s", branch, name)
                else:
                    logger.info("  └─ " + t("services.summarization.ollama.available_models"))
                    for i, name in enumerate(models):
                        branch = "├─" if i < len(models) - 1 else "└─"
                        logger.info("     %s %s", branch, name)
            else:
                if has_model_check:
                    logger.info("  ├─ " + t("services.summarization.ollama.connection_ok"))
                    logger.info("  ├─ " + t("services.summarization.ollama.no_models"))
                else:
                    logger.info("  └─ " + t("services.summarization.ollama.connection_ok"))

            # ── 4. Verify specified model ─────────────────────────
            if has_model_check:
                exists = client.check_model(model_name, quiet=True)
                status_key = "services.summarization.ollama.model_exists" if exists else "services.summarization.ollama.model_not_exists"
                logger.info(
                    "  └─ " + t("services.summarization.ollama.model_status", name=model_name, status=t(status_key)),
                )
                return exists

            return True
        finally:
            client.close()

    def _post_with_retry(
        self, url: str, json: dict, timeout: int, stream: bool = False
    ) -> requests.Response:
        """POST request with retry (only retries non-streaming requests)."""
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._session.post(
                    url, json=json, timeout=timeout, stream=stream
                )
                return response
            except (
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as e:
                last_exc = e
                if stream or attempt == self.max_retries:
                    raise
                wait = 2**attempt
                logger.warning(
                    t("services.summarization.ollama.retry", attempt=attempt, max=self.max_retries, error=e, seconds=wait),
                )
                time.sleep(wait)
        raise last_exc or SummarizationError(t("services.summarization.ollama.unknown_error"))

    def check_connection(self, quiet: bool = False) -> bool:
        """Check Ollama connection

        Args:
            quiet: If True, suppress logs

        Returns:
            Whether connection was successful
        """
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            success = response.status_code == 200
            if not quiet:
                if success:
                    logger.info(t("services.summarization.ollama.check_connection_ok"))
                else:
                    try:
                        error_detail = response.json()
                        logger.error(
                            t("services.summarization.ollama.check_connection_fail_detail", code=response.status_code, detail=error_detail),
                        )
                    except Exception:
                        logger.error(
                            t("services.summarization.ollama.check_connection_fail", code=response.status_code),
                        )
            return success
        except Exception as e:
            if not quiet:
                logger.error(t("services.summarization.ollama.check_connection_error", error=e))
            return False

    def list_models(self, quiet: bool = False) -> List[str]:
        """List available models

        Args:
            quiet: If True, suppress logs

        Returns:
            List of model names
        """
        try:
            response = self._session.get(f"{self.base_url}/api/tags", timeout=10)
            response.raise_for_status()

            data = response.json()
            models = [model["name"] for model in data.get("models", [])]

            if not quiet:
                logger.info(t("services.summarization.ollama.available_models_list", models=models))
            return models
        except Exception as e:
            if not quiet:
                logger.error(t("services.summarization.ollama.list_models_failed", error=e))
            return []

    def check_model(self, model_name: str, quiet: bool = False) -> bool:
        """Check if a specific model exists

        Args:
            model_name: Model name
            quiet: If True, suppress logs

        Returns:
            Whether the model exists
        """
        try:
            models = self.list_models(quiet=True)
            exists = model_name in models
            if not quiet:
                status_key = "services.summarization.ollama.model_exists" if exists else "services.summarization.ollama.model_not_exists"
                logger.info(t("services.summarization.ollama.model_check_log", name=model_name, status=t(status_key)))
                if not exists:
                    logger.warning(t("services.summarization.ollama.available_models_list", models=models))
            return exists
        except Exception as e:
            if not quiet:
                logger.error(t("services.summarization.ollama.check_model_failed", error=e))
            return False

    def generate(
        self,
        model: str,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        pause_event: Optional[threading.Event] = None,
    ) -> str:
        """Generate text

        Args:
            model: Model name
            prompt: Input prompt
            system_prompt: System prompt
            temperature: Temperature parameter
            max_tokens: Maximum number of tokens
            stream: Whether to stream output
            on_token: Token callback for streaming output
            cancel_check: Cancel check callback, returns True to interrupt
            pause_event: Pause control event, set() to continue, clear() to pause

        Returns:
            Generated text
        """
        payload = {"model": model, "prompt": prompt, "stream": stream}

        if system_prompt:
            payload["system"] = system_prompt

        if temperature is not None:
            payload["options"] = {"temperature": temperature}

        if max_tokens is not None:
            if "options" not in payload:
                payload["options"] = {}
            payload["options"]["num_predict"] = max_tokens

        logger.debug("Ollama request params: %s", payload)

        try:
            response = self._post_with_retry(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=self.timeout,
                stream=stream,
            )

            with response:
                logger.debug("Ollama response status: %s", response.status_code)

                if response.status_code != 200:
                    error_msg = t("services.summarization.ollama.api_error", code=response.status_code)
                    try:
                        error_detail = response.json()
                        error_msg += f", {error_detail}"
                    except Exception:
                        error_msg += f", {response.text[:200]}"
                    logger.error("%s", error_msg)
                    raise SummarizationError(error_msg)

                if stream:
                    result = ""
                    for line in response.iter_lines():
                        if cancel_check and cancel_check():
                            raise SummarizationError(t("services.summarization.ollama.user_cancelled"))
                        if line:
                            try:
                                data = _json.loads(line)
                            except _json.JSONDecodeError:
                                logger.warning(
                                    t("services.summarization.ollama.stream_json_error", line=line[:200])
                                )
                                continue
                            if "error" in data:
                                raise SummarizationError(
                                    t("services.summarization.ollama.stream_error", error=data["error"])
                                )
                            if "response" in data:
                                token = data["response"]
                                result += token
                                if on_token:
                                    on_token(token)
                            if data.get("done", False):
                                break
                    return result
                else:
                    data = response.json()
                    return data.get("response", "")

        except requests.exceptions.Timeout:
            raise SummarizationError(t("services.summarization.ollama.request_timeout"))
        except requests.exceptions.RequestException as e:
            raise SummarizationError(t("services.summarization.ollama.request_failed", error=e))
        except SummarizationError:
            raise
        except Exception as e:
            raise SummarizationError(t("services.summarization.ollama.generate_failed", error=e))
