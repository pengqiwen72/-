"""
LangGraph 客户端封装
"""
import asyncio
import sys
from rich.console import Console
from rich.panel import Panel
from cs_agent_api import CsAgentAPI
from langgraph.types import Command

server_url = "http://127.0.0.1:9123"

async def test():
    """查看EcomCS Agent 的运行状态"""
    console = Console()

    # 创建客户端实例
    client = CsAgentAPI()


    console.print(Panel(
        "🚀 Cs Agent 运行状态查看\n"
        "这个演示将展示如何使用客户端连接服务器并查看Cs Agent 的运行状态",
        title="欢迎使用 EcomCS Agent 运行状态查看",
        border_style="blue"
    ))
    
    
    try:
        # 连接到服务器
        console.print("\n[bold blue]连接到服务器[/bold blue]")
        success = await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
        


        if not success:
            console.print("[red]无法连接到服务器，请确保服务器正在运行[/red]")
            return
        
        # 列出可用助手
        console.print("\n[bold blue]获取可用助手列表[/bold blue]")
        assistants = await client.list_assistants()
        
        if assistants:
            console.print(f"[green]找到 {len(assistants)} 个助手[/green]")
            for i, assistant in enumerate(assistants[:30]):  # 只显示前30个
                console.print(f"  {i+1}. {assistant.get('name', '未命名助手')}")
                console.print(assistant)
        else:
            console.print("[yellow]没有找到可用的助手[/yellow]")
        
        # 列出现有线程
        console.print("\n[bold blue]获取现有线程[/bold blue]")
        threads = await client.list_threads()
        
        if threads:
            console.print(f"[green]找到 {len(threads)} 个线程[/green]")
            for i, thread in enumerate(threads[:10]):  # 只显示前10个
                console.print(f"  {i+1}. thread_id: {thread.get('thread_id')}")
                console.print(f"         status   : {thread.get("status")}")
                interrupts = thread.get("interrupts")
                if interrupts:
                    keys = list(interrupts.keys())
                    for key in keys:
                        console.print(f"         {key}: {len(interrupts[key])}")
                else:
                    console.print(f"         interrupts: 无")
        else:
            console.print("[yellow]没有找到现有线程[/yellow]")


        # 列出所有的运行
        console.print("\n[bold blue]获取所有运行[/bold blue]")

        for thread in threads:
            thread_id = thread.get('thread_id')
            runs = await client.list_runs(thread_id=thread_id)
            if runs:
                console.print(f"[green]找到 {len(runs)} 个运行[/green]")
                for i, run in enumerate(runs[:10]):  # 只显示前10个
                    console.print(f"  {i+1}. run_id: {run.get('run_id')}")

                    ## 运行的状态。可以是“pending”、“running”、“error”、“success”、“timeout”、“interrupted”之一。
                    status = run.get('status')
                    if status == "pending":
                        console.print(f"         status: [yellow]{status}[/yellow]")
                    elif status == "running":
                        console.print(f"         status: [green]{status}[/green]")
                    elif status == "error":
                        console.print(f"         status: [red]{status}[/red]")
                    elif status == "success":
                        console.print(f"         status: [green]{status}[/green]")
                    elif status == "timeout":
                        console.print(f"         status: [red]{status}[/red]")
                    elif status == "interrupted":
                        console.print(f"         status: [red]{status}[/red]")
                    console.print(f"         status: {run.get('status')}")
                    console.print(f"         thread_id: {run.get('thread_id')}")
                    console.print(f"         assistant_id: {run.get('assistant_id')}")
    except Exception as e:
        console.print(f"\n[red]演示过程中出现错误: {e}[/red]")
        console.print("[yellow]请检查服务器是否正在运行，以及配置是否正确[/yellow]")
    
    finally:
        # 断开连接
        await client.disconnect()


async def test_profile_suggestion():
    """测试profile_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "profile_suggestion"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")

async def test_tag_suggestion():
    """测试tag_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "tag_suggestion"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")

async def test_schedule_suggestion():
    """测试schedule_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "schedule_suggestion"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")

async def test_chat_suggestion():
    """测试chat_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "chat_suggestion"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")

async def test_kf_chat_suggestion():
    """测试kf_chat_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "kf_chat_suggestion"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")

async def test_talk_suggestion():
    """测试talk_suggestion"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.create_thread(external_id="wx_ext_001", user_id="CS001")
    stream = await client.create_stream_run(thread_id=thread_id, assistant_id=assistant_id, input={"task_input": "分析一下最近酱香酒特点"})
    async for chunk in stream:
        console.print(chunk)
    console.print(f"[green]任务完成[/green]")


async def get_interrupt():
    """获取线程状态"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")

    thread_id = await client.get_thread_id(user_id="CS001", external_id="wx_ext_001")
    interrupts   = await client.get_interrupts_from_thread(thread_id=thread_id)
    if interrupts and len(interrupts) > 0:
        interrupt = interrupts[0]
        console.print(f"[green]获取到中断信息[/green]")
        console.print(interrupt)
    else:
        console.print("[yellow]没有找到中断信息[/yellow]")
    
async def resume_interrupt():
    """恢复线程"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.get_thread_id(user_id="CS001", external_id="wx_ext_001")

    comfirmed = {"confirmed": "ok"}
    command = {"resume": comfirmed}

    stream = await client.resume_interrupt_run(thread_id=thread_id, assistant_id=assistant_id, command=command)
    async for chunk in stream:
        console.print(chunk)

async def resume_interrupt_with_confirmed(confirmed: str = "ok"):
    """恢复中断"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    assistant_id = await client.create_assistant(graph_id="cs", external_id="wx_ext_001", user_id="CS001")
    thread_id = await client.get_thread_id(user_id="CS001", external_id="wx_ext_001")

    confirmed = {"confirmed": confirmed}
    command = {"resume": confirmed}
    stream = await client.resume_interrupt_run(thread_id=thread_id, assistant_id=assistant_id, command=command)
    async for chunk in stream:
        console.print(chunk)

async def delete_thread(thread_id: str):
    """删除线程"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    res = await client.delete_thread(thread_id=thread_id)
    if res:
        console.print(f"[green]线程 {thread_id} 删除成功[/green]")
    else:
        console.print(f"[red]线程 {thread_id} 删除失败[/red]")

async def delete_all_threads():
    """删除所有线程"""
    console = Console()
    client = CsAgentAPI()
    await client.connect(cs_server_url=server_url, cs_user="CS001", password="123456")
    threads = await client.list_threads()
    for thread in threads:
        ret = await client.delete_thread(thread_id=thread.get('thread_id'))
        if ret:
            console.print(f"[green]线程 {thread.get('thread_id')} 删除成功[/green]")
        else:
            console.print(f"[red]线程 {thread.get('thread_id')} 删除失败[/red]")
    console.print(f"[green]任务完成[/green]")


if __name__ == "__main__":
    ## 获取输入参数 根据参数选择测试函数
    ## 测试函数：test_profile_suggestion
    ## 测试函数：get_thread_state
    ## 测试函数：get_interrupt
    ## 测试函数：resume_interrupt
    ## 测试函数：delete_thread
    ## 测试函数：test
    if len(sys.argv) > 1:
        if sys.argv[1] == "profile":
            asyncio.run(test_profile_suggestion())
        elif sys.argv[1] == "tag":
            asyncio.run(test_tag_suggestion())
        elif sys.argv[1] == "schedule":
            asyncio.run(test_schedule_suggestion())
        elif sys.argv[1] == "chat":
            asyncio.run(test_chat_suggestion())
        elif sys.argv[1] == "kf_chat":
            asyncio.run(test_kf_chat_suggestion())
        elif sys.argv[1] == "talk":
            asyncio.run(test_talk_suggestion())
        elif sys.argv[1] == "get_interrupt":
            asyncio.run(get_interrupt())
        elif sys.argv[1] == "resume_interrupt":
            asyncio.run(resume_interrupt_with_confirmed())
        elif sys.argv[1] == "resume_confirmed":
            asyncio.run(resume_interrupt_with_confirmed(confirmed=sys.argv[2]))
        elif sys.argv[1] == "delete_thread":
            asyncio.run(delete_thread(thread_id=sys.argv[2]))
        elif sys.argv[1] == "delete_all_threads":
            asyncio.run(delete_all_threads())
        elif sys.argv[1] == "test":
            asyncio.run(test())
        else:
            print("请输入正确的测试函数名称")
    else:
        print("请输入测试函数名称")
        print("python demo_agent_test.py profile")
        print("python demo_agent_test.py tag")
        print("python demo_agent_test.py schedule")
        print("python demo_agent_test.py chat")
        print("python demo_agent_test.py kf_chat")
        print("python demo_agent_test.py talk")
        print("python demo_agent_test.py get_interrupt")
        print("python demo_agent_test.py resume_interrupt")
        print("python demo_agent_test.py resume_confirmed confirmed")
        print("python demo_agent_test.py delete_thread thread_id")
        print("python demo_agent_test.py delete_all_threads")
        print("python demo_agent_test.py test")
