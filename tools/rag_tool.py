from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain.tools import tool
import os

@tool
def company_knowledge_search(query: str) -> str:
    """从企业内部文档中检索相关知识和政策"""
    docs_path = "docs"
    if not os.path.exists(docs_path):
        return "文档目录不存在，请先放入企业文档。"

    loader = DirectoryLoader(docs_path, glob="**/*.*", loader_cls=TextLoader, loader_kwargs={"encoding": "utf-8"})
    documents = loader.load()

    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    splits = text_splitter.split_documents(documents)

    embeddings = OpenAIEmbeddings()
    vectorstore = Chroma.from_documents(
        documents=splits,
        embedding=embeddings,
        persist_directory="./chroma_db"
    )

    retriever = vectorstore.as_retriever(search_kwargs={"k": 4})
    results = retriever.invoke(query)
    return "\n\n".join([doc.page_content for doc in results])