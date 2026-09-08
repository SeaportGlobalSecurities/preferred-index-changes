# DEPRECATED (2026-09-08): Local scheduling retired.
# The tracker now runs exclusively via GitHub Actions (.github/workflows/daily.yml)
# at 6:30 AM ET, with an automatic 9:30 AM ET retry when the FT Portfolios site
# hasn't updated its "as of" date yet.
#
# If a Windows Task Scheduler job still points here, delete it:
#   Get-ScheduledTask | Where-Object { $_.Actions.Arguments -match 'run_daily' }
#   Unregister-ScheduledTask -TaskName "<name from above>" -Confirm:$false
Write-Warning "run_daily.ps1 is deprecated - the FPE tracker runs via GitHub Actions only. Delete the local Task Scheduler job."
exit 0
