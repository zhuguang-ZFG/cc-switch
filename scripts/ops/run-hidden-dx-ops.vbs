' run-hidden-dx-ops.vbs — 隐藏窗口启动 newapi-dx-analyze.bat（供任务计划程序调用）
' 2026-08-10: 直启 bat（cmd /c 包装在 Run 下不执行）；透传退出码给任务计划程序。
Dim sh, code
Set sh = CreateObject("Wscript.Shell")
code = sh.Run("""D:\Users\cc-switch\scripts\ops\newapi-dx-analyze.bat""", 0, True)
WScript.Quit code
