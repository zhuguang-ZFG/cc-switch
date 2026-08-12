param(
    [string]$Model = "zg-claude-opus-5",
    [string]$Base = "http://127.0.0.1:3002/v1",
    [int]$TimeoutSec = 90
)
$cfg = Get-Content C:\Users\zhugu\.omp\agent\models.yml -Raw
$m = [regex]::Match($cfg, 'zg-newapi:\s*\r?\n\s*baseUrl:[\s\S]*?apiKey:\s*(\S+)')
if (-not $m.Success) { Write-Output "NO_KEY"; exit 1 }
$key = $m.Groups[1].Value
$body = (@{ model = $Model; max_tokens = 8; stream = $true; messages = @(@{ role = "user"; content = "hi" }) } | ConvertTo-Json -Depth 5 -Compress)
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $req = [System.Net.HttpWebRequest]::Create("$Base/chat/completions")
    $req.Method = 'POST'
    $req.ContentType = 'application/json'
    $req.Headers.Add('Authorization', "Bearer $key")
    $req.Timeout = $TimeoutSec * 1000
    $req.ReadWriteTimeout = $TimeoutSec * 1000
    $bytes = [Text.Encoding]::UTF8.GetBytes($body)
    $req.ContentLength = $bytes.Length
    $st = $req.GetRequestStream(); $st.Write($bytes, 0, $bytes.Length); $st.Close()
    $resp = $req.GetResponse()
    $ttfb = $sw.ElapsedMilliseconds
    $sr = New-Object IO.StreamReader($resp.GetResponseStream())
    $line = $sr.ReadLine()
    $total = $sw.ElapsedMilliseconds
    $preview = if ($line) { $line.Substring(0, [Math]::Min(100, $line.Length)) } else { "(empty)" }
    Write-Output ("{0} HTTP={1} TTFB_ms={2} first_line_total_ms={3} preview={4}" -f $Model, [int]$resp.StatusCode, $ttfb, $total, $preview)
} catch {
    $errMs = $sw.ElapsedMilliseconds
    $webEx = $_.Exception.InnerException -as [System.Net.WebException]
    if ($webEx -and $webEx.Response) {
        $errSr = New-Object IO.StreamReader($webEx.Response.GetResponseStream())
        $errBody = $errSr.ReadToEnd().Substring(0, [Math]::Min(400, $errSr.BaseStream.Length))
        $errSr.Close()
        Write-Output ("{0} ERROR after {1}ms HTTP={2} body={3}" -f $Model, $errMs, [int]($webEx.Response -as [System.Net.HttpWebResponse]).StatusCode, $errBody)
    } else {
        Write-Output ("{0} ERROR after {1}ms: {2}" -f $Model, $errMs, $_.Exception.Message)
    }
}
