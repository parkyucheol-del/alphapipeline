# Cross-reference each external payer's exact USDC amount against the
# current AlphaPipeline price table to guess which endpoint(s) they likely
# called. Price tiers are ambiguous when multiple endpoints share a price
# (esp. $0.01 and $0.02), so this gives candidate endpoints, not certainty.
# Reuses the same Blockscout tokentx call as count_payers.ps1.

$PAYTO = "0x2bcfA4F7E1fA5E96934846A7dEa623398Ed7f752"
$USDC  = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
# 전체 주소를 아는 지갑은 여기, 접두사만 아는 지갑(세션 로그에 잘려서 기록된 것)은
# 아래 $MY_WALLET_PREFIXES에 넣는다.
$MY_WALLETS = @(
    "0x98B73557e7992b88723CC9eE8ABB6C880418f4cf"
)
$MY_WALLET_PREFIXES = @(
    "0x8d2a47cb"  # 사용자 본인 테스트 지갑으로 이전 세션에서 확인됨 (전체 주소 미보유)
)

# Price table as of 2026-10-18 (USDC). Add new prices here if config.py changes.
$PRICE_MAP = @{
    0.001 = @("derivatives.funding_rate_value", "market.oracle_gap_bp")
    0.002 = @("prediction.hip4_price")
    0.005 = @("tools.ai_markdown")
    0.01  = @("derivatives.funding_rate", "derivatives.funding_apr_matrix", "calendar.macro_dday",
              "security.token_prescreen", "security.token_authenticity", "prediction.hip4_snapshot",
              "prediction.hip4_price_ladder", "market.cycle_compare", "market.oracle_gap")
    0.02  = @("dex.liquidity_slippage", "security.token_risk", "arb.spread_matrix",
              "security.contract_health_audit", "derivatives.whale_position_audit",
              "prediction.exit_capacity_audit")
    0.03  = @("security.token_diagnostic", "prediction.neg_risk_arbitrage")
    0.05  = @("prediction.hip4_alerts_subscribe")
}

$url = "https://base.blockscout.com/api?module=account&action=tokentx&contractaddress=$USDC&address=$PAYTO&page=1&offset=1000&sort=desc"
$resp = Invoke-RestMethod -Uri $url -Method Get
if ($resp.status -ne "1" -and $resp.message -notmatch "No transactions found") {
    Write-Host "API 응답 이상: status=$($resp.status) message=$($resp.message)"
}

# 프로젝트 시작일(2026-09-01) 이전 거래는 무관한 거래(예: 2025-02-25, 11,592.42 USDC
# 1건 - 이전 세션에서 프로젝트와 무관으로 확정)이므로 제외.
$PROJECT_START_UNIX = [DateTimeOffset]::new(2026, 9, 1, 0, 0, 0, [TimeSpan]::Zero).ToUnixTimeSeconds()

$txs = $resp.result | Where-Object { $_.to -eq $PAYTO.ToLower() -or $_.to -eq $PAYTO }
$external = $txs | Where-Object {
    $from = $_.from
    $isMine = ($MY_WALLETS -contains $from) -or ($MY_WALLET_PREFIXES | Where-Object { $from -like "$_*" })
    $isBeforeStart = [long]$_.timeStamp -lt $PROJECT_START_UNIX
    (-not $isMine) -and (-not $isBeforeStart)
}

Write-Host "=== 외부 결제 $($external.Count)건, 금액별 추정 엔드포인트 ==="
Write-Host ""

$external | Sort-Object timeStamp | ForEach-Object {
    $amount = [math]::Round([decimal]$_.value / 1000000, 6)
    $ts = [DateTimeOffset]::FromUnixTimeSeconds([long]$_.timeStamp).ToString("yyyy-MM-dd HH:mm")
    $candidates = $PRICE_MAP[[double]$amount]
    if (-not $candidates) {
        $candidates = @("(가격표에 없는 금액 - 수동 확인 필요)")
    }
    $candidateStr = $candidates -join ", "
    Write-Host ("{0}  {1}  {2:N6} USDC  -> {3}" -f $ts, $_.from, $amount, $candidateStr)
}

Write-Host ""
Write-Host "=== 금액별 집계 (빈도순) ==="
$external | Group-Object { [math]::Round([decimal]$_.value / 1000000, 6) } | Sort-Object Count -Descending | ForEach-Object {
    $amt = [double]$_.Name
    $candidates = $PRICE_MAP[$amt]
    $candidateStr = if ($candidates) { $candidates -join ", " } else { "(미확인 금액)" }
    Write-Host ("{0} USDC x {1}건  -> 후보: {2}" -f $_.Name, $_.Count, $candidateStr)
}
