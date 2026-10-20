from pathlib import Path

from ..coding.project_context import ProjectContextFile


def build_system_prompt(
    cwd: Path,
    tool_names: list[str],
    context_files: list[ProjectContextFile],
    *,
    supports_images: bool = False,
) -> str:
    base_prompt = (
        f"你是一个 coding agent，工作目录是 {cwd}。\n"
        f"当前可用工具：{', '.join(tool_names)}。\n"
        "先读取相关文件，再进行修改，以实际工具结果为依据。\n"
        "用 edit 精确修改，用 write 创建文件或完整重写。"
        "edit 参数是 path 和 edits；每项包含 oldText、newText。"
        "同一批替换针对原文件定位，oldText 必须唯一且互不重叠。\n"
        "生成较大文件时，先写可运行的骨架，再用 edit 分批补充，"
        "避免一次工具调用携带过长内容。只有收到工具结果后，才能判断修改是否成功。\n"
        "bash 工具使用 Bash 语法，powershell 工具使用 PowerShell 语法。\n"
        "如果历史中的工具结果说明执行状态未知，先检查实际文件、日志或进程状态，"
        "再决定下一步。\n"
        "切换对话分支不会撤销项目文件的修改。\n"
        "完成任务后简要说明修改、依据以及尚未完成的工作。使用中文回答。"
    )
    if supports_images:
        base_prompt += (
            "\n当前模型配置已启用图片理解。用户给出本地图片路径时，"
            "先调用 read 取得图片附件，再根据实际图像回答，不要仅凭路径声称无法看图。"
            "支持 PNG、JPEG、GIF 和 WebP，其他图片格式需先转换。"
            "SVG 和 HTML 是源码；分析源码不等于已经看到浏览器渲染结果，"
            "需要视觉检查时应取得截图后读取。"
        )
    else:
        base_prompt += "\n当前模型配置未启用图片输入；读取图片得到的附件不会发送给模型。"
    if not context_files:
        return base_prompt

    sections = [base_prompt, "以下是当前项目的工作约定，请在执行任务时遵守。"]
    for context_file in context_files:
        sections.append(f"项目指令来源：{context_file.path}\n\n{context_file.content}")
    return "\n\n".join(sections)
