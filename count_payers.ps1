$PAYTO = "0x2bcfA4F7E1fA5E96934846A7dEa623398Ed7f752"
$USDC  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
$MY_WALLETS = @(
    "0x98B73557e7992b88723CC9eE8ABB6C880418f4cf"
)
$url = "https://base.blockscout.com/api?module=account&action=tokentx&contractaddress=$USDC&address=$PAYTO&page=1&offset=1000&sort=desc"
$resp = Invoke-RestMethod -Uri $url -Method Get
if ($resp.status -ne "1" -and $resp.message -notmatch "No transactions found") {
    Write-Host "API 응답 이상: status=$($resp.status) message=$($resp.message)"
}
$txs = $resp.result | Where-Object { $_.to -eq $PAYTO.ToLower() -or $_.to -eq $PAYTO }
Write-Host "=== 전체 ==="
Write-Host "총 전송 건수: $($txs.Count)"
$external = $txs | Where-Object { $MY_WALLETS -notcontains $_.from }
Write-Host ""
Write-Host "=== 내 지갑 제외한 외부 전송 ==="
Write-Host "외부 전송 건수: $($external.Count)"
$uniquePayers = $external | Select-Object -ExpandProperty from -Unique
Write-Host "외부 고유 지불자 수: $($uniquePayers.Count)"
$totalUsdc = ($external | ForEach-Object { [decimal]$_.value / 1000000 } | Measure-Object -Sum).Sum
Write-Host ("외부 전송 총액: {0:N6} USDC" -f $totalUsdc)
Write-Host ""
Write-Host "=== 지갑별 집계 (건수 내림차순) ==="
$external | Group-Object from | Sort-Object Count -Descending | ForEach-Object {
    $sum = ($_.Group | ForEach-Object { [decimal]$_.value / 1000000 } | Measure-Object -Sum).Sum
    $firstTs = ($_.Group | Sort-Object timeStamp | Select-Object -First 1).timeStamp
    $firstDate = [DateTimeOffset]::FromUnixTimeSeconds([long]$firstTs).ToString("yyyy-MM-dd HH:mm")
    Write-Host ("{0}  건수={1,-4} 합계={2,10:N6} USDC  첫거래={3}" -f $_.Name, $_.Count, $sum, $firstDate)
}
