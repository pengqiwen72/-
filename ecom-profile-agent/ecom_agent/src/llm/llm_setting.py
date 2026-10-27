from langchain.chat_models import init_chat_model
import os
from dotenv import load_dotenv

load_dotenv()

chat_model = init_chat_model(
    model_provider=os.getenv("MODEL_PROVIDER"),
    model=os.getenv("MODEL_NAME"),
    base_url=os.getenv("BASE_URL"),
    api_key=os.getenv("API_KEY"),
)