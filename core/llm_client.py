"""
LLM客户端 — 统一封装大模型API调用
支持OpenAI兼容接口（GPT / DeepSeek等）
"""
import logging

logger = logging.getLogger("ReviewGuard.LLM")


class LLMClient:
    """LLM客户端，封装API调用"""

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.api_key = self.config.get("api_key", "")
        self.base_url = self.config.get("base_url", "https://api.openai.com/v1")
        self.model = self.config.get("model", "gpt-3.5-turbo")
        self.max_tokens = self.config.get("max_tokens", 1024)
        self.temperature = self.config.get("temperature", 0.3)

        self._client = None
        if self.api_key:
            self._init_client()

    def _init_client(self):
        """初始化OpenAI客户端"""
        try:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url if self.base_url else None,
            )
            logger.info(f"LLM客户端初始化成功, model={self.model}")
        except ImportError:
            logger.warning("openai包未安装，LLM功能不可用。pip install openai")
        except Exception as e:
            logger.error(f"LLM客户端初始化失败: {e}")

    def generate(self, prompt: str, system_prompt: str = None) -> str:
        """
        生成文本

        Args:
            prompt: 用户提示词
            system_prompt: 系统提示词（可选）

        Returns:
            生成的文本
        """
        if not self._client:
            logger.warning("LLM客户端未初始化，返回空字符串")
            return ""

        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            result = response.choices[0].message.content.strip()
            logger.debug(f"LLM生成成功, 长度={len(result)}")
            return result

        except Exception as e:
            logger.error(f"LLM生成失败: {e}")
            return ""
