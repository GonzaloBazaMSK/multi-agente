from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from agents.post_sales.prompts import POST_SALES_SYSTEM_PROMPT
from agents.post_sales.tools import (
    get_student_info,
    send_nps_survey,
)
from config.settings import get_settings

POST_SALES_TOOLS = [
    get_student_info,
    send_nps_survey,
]


def build_post_sales_agent(country: str = "AR"):
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.openai_model,
        api_key=settings.openai_api_key,
        temperature=0.2,
    )
    system_prompt = POST_SALES_SYSTEM_PROMPT.format(country=country)
    agent = create_react_agent(
        model=llm,
        tools=POST_SALES_TOOLS,
        prompt=SystemMessage(content=system_prompt),
    )
    return agent
