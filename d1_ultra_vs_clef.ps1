# AlphaPipeline — Liquid AI d1-ultra로 10/12 Clef-flash 6개 합성 시나리오 재검증
# (session-log-2026-10-12-clef-flash-hip4-noise-filter.md와 동일한 6개 시나리오,
#  같은 입력을 d1-ultra에 넣어서 Clef-flash와 나란히 비교)
#
# 실행 전: Liquid AI 계정 만들고 API 키 발급받아서 아래에 넣으세요.
# https://www.liquid.ai (또는 10/18 데일리 알파 카드의 블로그 링크 참고)

$LIQUID_API_KEY = "liquid_AR5r6jjN3p1N3JvwV0dCs6JKbOJLCcmnqwvXsQn5Be9uuucF"

$scenarios = @(
    @{ name = "얇은거래량·뉴스없음·작은스윙(3pp)";   clef_signal = 0.087; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.43 (3 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. No major news or headlines in the last hour related to this market." }
    @{ name = "얇은거래량·뉴스없음·큰스윙(13pp)";     clef_signal = 0.221; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. No major news or headlines in the last hour related to this market." }
    @{ name = "큰거래량·뉴스있음·큰스윙";             clef_signal = 0.792; clef_action = "alert";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). There IS major breaking news/headlines in the last hour directly related to this market." }
    @{ name = "큰거래량·뉴스없음·큰스윙";             clef_signal = 0.438; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). No major news or headlines in the last hour related to this market." }
    @{ name = "얇은거래량·뉴스있음·큰스윙";           clef_signal = 0.728; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. There IS major breaking news/headlines in the last hour directly related to this market." }
    @{ name = "큰거래량·뉴스있음·작은스윙";           clef_signal = 0.608; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above `$85k by Oct 31. YES probability moved from 0.40 to 0.43 (3 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). There IS major breaking news/headlines in the last hour directly related to this market." }
)

$results = @()

foreach ($s in $scenarios) {
    $body = @{
        model     = "d1-ultra"
        state     = $s.state
        questions = @{
            real_signal = @{ type = "noul"; instructions = "Is this probability move likely driven by real information rather than thin-liquidity noise?" }
            alert       = @{ type = "noul"; instructions = "Should a trading agent be alerted about this move?" }
        }
    } | ConvertTo-Json -Depth 5

    try {
        $resp = Invoke-RestMethod -Uri "https://api.liquid.ai/decisions/v1/systemone" -Method Post `
            -Headers @{ "Authorization" = "Bearer $LIQUID_API_KEY"; "Content-Type" = "application/json" } `
            -Body $body

        $d1Signal = $resp.answers.real_signal.noul
        $d1Alert  = $resp.answers.alert.noul

        $results += [PSCustomObject]@{
            시나리오     = $s.name
            Clef_signal  = $s.clef_signal
            Clef_action  = $s.clef_action
            d1_real_signal = $d1Signal
            d1_alert_noul  = $d1Alert
            오차_signal  = [math]::Round([math]::Abs($s.clef_signal - $d1Signal), 4)
        }
    } catch {
        Write-Host "[에러] $($s.name): $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "=== Clef-flash vs d1-ultra 비교 (10/12 합성 시나리오 6개) ==="
$results | Format-Table -AutoSize
$results | Export-Csv -Path "d1_ultra_vs_clef_results.csv" -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host "CSV로도 저장됨: d1_ultra_vs_clef_results.csv"
