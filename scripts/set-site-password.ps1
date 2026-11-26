<#
대시보드 아이디/비밀번호 설정 (또는 변경)

    powershell -ExecutionPolicy Bypass -File scripts\set-site-password.ps1

- 아이디와 비밀번호를 물어본 뒤 sha256("아이디:비밀번호")만 CloudFront KeyValueStore에 저장한다.
  비밀번호 원문은 어디에도 저장되지 않는다.
- 반영까지 수 초~1분 걸린다. 브라우저가 예전 로그인 정보를 기억하면 창을 모두 닫았다가 다시 연다.
#>
param(
    [string]$AwsProfile = "ai-news",
    [string]$Region = "ap-northeast-2",
    [string]$Stack = "AiNewsDashboard"
)
$ErrorActionPreference = "Stop"

$user = Read-Host "아이디 (영문/숫자, 콜론(:) 불가)"
if (-not $user -or $user.Contains(":")) { throw "아이디가 비었거나 콜론(:)이 들어 있습니다." }
$sec1 = Read-Host "비밀번호 (12자 이상 권장)" -AsSecureString
$sec2 = Read-Host "비밀번호 확인" -AsSecureString

function ConvertFrom-Secure([Security.SecureString]$s) {
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
$p1 = ConvertFrom-Secure $sec1; $p2 = ConvertFrom-Secure $sec2
if ($p1 -ne $p2) { throw "두 비밀번호가 다릅니다." }
if ($p1.Length -lt 8) { throw "비밀번호는 8자 이상으로 해 주세요." }

$sha = [Security.Cryptography.SHA256]::Create()
$hash = ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("${user}:${p1}")) | ForEach-Object { $_.ToString("x2") }) -join ""
$p1 = $null; $p2 = $null

$arn = aws cloudformation describe-stacks --stack-name $Stack --profile $AwsProfile --region $Region `
    --query "Stacks[0].Outputs[?OutputKey=='SiteAuthStoreArn'].OutputValue" --output text
if (-not $arn -or $arn -eq "None") { throw "스택 출력 SiteAuthStoreArn을 찾지 못했습니다. 배포가 끝났는지 확인하세요." }

$etag = aws cloudfront-keyvaluestore describe-key-value-store --kvs-arn $arn --profile $AwsProfile --query ETag --output text
aws cloudfront-keyvaluestore put-key --kvs-arn $arn --key auth_sha256 --value $hash --if-match $etag --profile $AwsProfile | Out-Null

Write-Host "완료: 아이디 '$user'로 설정했습니다. 1분쯤 뒤 대시보드를 새로 열어 로그인하세요." -ForegroundColor Green
