from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from port_tariff.config import get_settings
from port_tariff.logging_config import get_logger

logger = get_logger(__name__)

_embeddings_logged = False


def get_embeddings() -> Embeddings:
    """Initializes LangChain OpenAIEmbeddings directly connected to Hugging Face TEI (BGE-M3).

    Zero silent fallbacks: fails explicitly if the microservice is unreachable.
    """
    global _embeddings_logged
    settings = get_settings()

    if not _embeddings_logged:
        logger.info(
            "embeddings.client.initialized",
            base_url=settings.embedding_api_base,
            model=settings.embedding_model_name,
        )
        _embeddings_logged = True

    return OpenAIEmbeddings(
        model=settings.embedding_model_name,
        openai_api_base=settings.embedding_api_base,
        openai_api_key="none",
        check_embedding_ctx_length=False,
        chunk_size=4,  # Respects TEI's max-client-batch-size
    )
