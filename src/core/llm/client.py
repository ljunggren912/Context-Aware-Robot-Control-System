"""
LLM Client - Unified Interface for Language Model Providers
Supports OpenAI and Ollama with consistent API.
"""

import os
from typing import Optional
from src.core.observability.logging import get_logger

logger = get_logger("llm_client")


class LLMClient:
    """
    Unified interface for LLM providers.
    Automatically selects provider based on MODEL_PROVIDER env var.
    """
    
    def __init__(self):
        """Initialize LLM client with provider from environment."""
        self.provider = os.getenv("MODEL_PROVIDER", "openai")
        
        if self.provider == "openai":
            self.model_name = os.getenv("MODEL_NAME")
            self.api_key = os.getenv("OPENAI_API_KEY")
            
            if not self.model_name:
                raise ValueError("MODEL_NAME not set in .env file")
            if not self.api_key:
                raise ValueError("OPENAI_API_KEY not set in .env file")
                
        elif self.provider == "ollama":
            self.model_name = os.getenv("OLLAMA_MODEL")
            self.base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
            
            if not self.model_name:
                raise ValueError("OLLAMA_MODEL not set in .env file")
        else:
            raise ValueError(f"Unknown MODEL_PROVIDER: {self.provider}")
    
    def generate(
        self,
        prompt: str,
        correlation_id: str,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Generate text completion from prompt.
        
        Args:
            prompt: Input prompt for the LLM
            correlation_id: For logging/tracing
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate (provider default if None)
        
        Returns:
            Generated text response
        """
        logger.info(
            "Generating LLM completion",
            correlation_id=correlation_id,
            provider=self.provider,
            model=self.model_name,
            temperature=temperature,
        )
        
        if self.provider == "openai":
            return self._openai_generate(prompt, correlation_id, temperature, max_tokens)
        elif self.provider == "ollama":
            return self._ollama_generate(prompt, correlation_id, temperature, max_tokens)
    
    def _openai_generate(
        self,
        prompt: str,
        correlation_id: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> str:
        """Call OpenAI API."""
        from openai import OpenAI
        
        client = OpenAI(api_key=self.api_key)
        
        kwargs = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        
        response = client.chat.completions.create(**kwargs)
        
        content = response.choices[0].message.content
        
        logger.info(
            "OpenAI response received",
            correlation_id=correlation_id,
            token_usage=response.usage.total_tokens,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )
        
        return content
    
    def _ollama_generate(
        self,
        prompt: str,
        correlation_id: str,
        temperature: float,
        max_tokens: Optional[int],
    ) -> str:
        """Call Ollama API."""
        import requests
        
        url = f"{self.base_url}/api/generate"
        
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "temperature": temperature,
            "stream": False,
        }
        
        if max_tokens:
            payload["options"] = {"num_predict": max_tokens}
        
        try:
            response = requests.post(url, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()
            
            logger.info(
                "Ollama response received",
                correlation_id=correlation_id,
                model=self.model_name,
            )
            
            return data.get("response", "")
            
        except requests.RequestException as e:
            logger.error(
                "Ollama API call failed",
                correlation_id=correlation_id,
                error=str(e),
            )
            raise
