# ACE Observability Work Order

This is the execution order for the observability/dashboard tickets that were created.

## Completion Order

1. **Implement structured lifecycle logging for ACE listener and worker**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/4  
   Why first: establishes the required normalized log schema used by all downstream work.

2. **Propagate correlation IDs through Pub/Sub and worker lifecycle logs**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/5  
   Blocked by: #4  
   Why second: depends on the base schema to carry correlation fields end-to-end.

3. **Provision log sink and BigQuery storage for ACE lifecycle observability**  
   Repo: `Day-in-the-Country-LLC/ditc_terraform`  
   Issue: https://github.com/Day-in-the-Country-LLC/ditc_terraform/issues/8  
   Blocked by: `appforge-poc#4`  
   Why third: should use the finalized lifecycle log fields when creating sink/query infra.

4. **Deliver end-to-end ACE operations dashboard and troubleshooting runbook**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/6  
   Blocked by: #5 and `ditc_terraform#8`  
   Why last: requires both complete correlation logging and analytics storage infrastructure.

## Dependency Chain Summary

`appforge-poc#4` -> `appforge-poc#5`  
`appforge-poc#4` -> `ditc_terraform#8`  
`appforge-poc#5` + `ditc_terraform#8` -> `appforge-poc#6`
