# AlphaPipeline - compare Liquid AI d1-ultra against the 2026-10-12 Clef-flash
# 6 synthetic scenarios (same inputs as session-log-2026-10-12-clef-flash-hip4-noise-filter.md)
#
# No need to edit this file for the key - it will ask for it when you run it.
# Get a key first at https://console.liquid.ai (API keys page).

$LIQUID_API_KEY = Read-Host "Paste your Liquid AI API key"

$scenarios = @(
    @{ name = "thin_vol-no_news-small_swing_3pp";  clef_signal = 0.087; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.43 (3 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. No major news or headlines in the last hour related to this market." }
    @{ name = "thin_vol-no_news-big_swing_13pp";   clef_signal = 0.221; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. No major news or headlines in the last hour related to this market." }
    @{ name = "big_vol-news-big_swing";            clef_signal = 0.792; clef_action = "alert";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). There IS major breaking news/headlines in the last hour directly related to this market." }
    @{ name = "big_vol-no_news-big_swing";         clef_signal = 0.438; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). No major news or headlines in the last hour related to this market." }
    @{ name = "thin_vol-news-big_swing";           clef_signal = 0.728; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.53 (13 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is thin. There IS major breaking news/headlines in the last hour directly related to this market." }
    @{ name = "big_vol-news-small_swing_3pp";      clef_signal = 0.608; clef_action = "watch";
       state = "Hyperliquid HIP-4 prediction market snapshot: BTC above 85k USD by Oct 31. YES probability moved from 0.40 to 0.43 (3 percentage points) in the last 10 minutes. Order book liquidity/volume in this market is large (high trading activity). There IS major breaking news/headlines in the last hour directly related to this market." }
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
            scenario       = $s.name
            clef_signal    = $s.clef_signal
            clef_action    = $s.clef_action
            d1_real_signal = $d1Signal
            d1_alert_noul  = $d1Alert
            abs_diff       = [math]::Round([math]::Abs($s.clef_signal - $d1Signal), 4)
        }
    } catch {
        Write-Host "[ERROR] $($s.name): $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "=== Clef-flash vs d1-ultra comparison (2026-10-12 synthetic scenarios) ==="
$results | Format-Table -AutoSize
$results | Export-Csv -Path "d1_ultra_vs_clef_results.csv" -NoTypeInformation -Encoding UTF8
Write-Host ""
Write-Host "Also saved to d1_ultra_vs_clef_results.csv"
