$modelsDir = Join-Path $PSScriptRoot "models"
New-Item -ItemType Directory -Force -Path $modelsDir | Out-Null

$models = @(
    @{ Name = "gemma-3-1b-it-Q4_K_M.gguf";              Url = "https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf" },
    @{ Name = "qwen2.5-coder-7b-instruct-q4_k_m.gguf";  Url = "https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" },
    @{ Name = "gemma-3-4b-it-Q4_K_M.gguf";              Url = "https://huggingface.co/bartowski/google_gemma-3-4b-it-GGUF/resolve/main/google_gemma-3-4b-it-Q4_K_M.gguf" },
    @{ Name = "Phi-4-mini-instruct-Q4_K_M.gguf";        Url = "https://huggingface.co/bartowski/microsoft_Phi-4-mini-instruct-GGUF/resolve/main/microsoft_Phi-4-mini-instruct-Q4_K_M.gguf" },
    @{ Name = "phi-4-Q4_K_M.gguf";                      Url = "https://huggingface.co/bartowski/phi-4-GGUF/resolve/main/phi-4-Q4_K_M.gguf" },
    @{ Name = "qwen2.5-coder-32b-instruct-IQ3_XS.gguf"; Url = "https://huggingface.co/bartowski/Qwen2.5-Coder-32B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-32B-Instruct-IQ3_XS.gguf" }
)

foreach ($m in $models) {
    $target = Join-Path $modelsDir $m.Name
    $part   = "$target.part"
    if ((Test-Path $target) -and (Get-Item $target).Length -gt 100MB) {
        Write-Host "Already installed: $($m.Name)" -ForegroundColor Green
        continue
    }
    Write-Host "Downloading $($m.Name)..." -ForegroundColor Cyan
    & curl.exe --fail --location --retry 5 --retry-delay 2 --continue-at - --output $part $m.Url
    if ($LASTEXITCODE -eq 0 -and (Get-Item $part).Length -gt 100MB) {
        Move-Item -Force $part $target
        Write-Host "Saved: $target" -ForegroundColor Green
    }
}
