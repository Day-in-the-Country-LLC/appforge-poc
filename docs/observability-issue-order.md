# ACE Observability Work Order

This is the execution order for the observability/dashboard tickets.

## Completion Order

1. **Implement structured lifecycle logging for ACE listener and worker**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/4  
   Status: closed  
   Why first: establishes the normalized lifecycle log schema.

2. **Propagate correlation IDs through Pub/Sub and worker lifecycle logs**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/5  
   Status: closed  
   Blocked by: `appforge-poc#4`  
   Why second: adds end-to-end correlation identifiers.

3. **Provision log sink and BigQuery storage for ACE lifecycle observability**  
   Repo: `Day-in-the-Country-LLC/ditc_terraform`  
   Issue: https://github.com/Day-in-the-Country-LLC/ditc_terraform/issues/8  
   Status: open (infra applied; issue still tracks final closure)  
   Blocked by: `appforge-poc#4`  
   Why third: provides durable analytics storage for lifecycle logs.

4. **Create ACE observability query pack for lifecycle analytics**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/9  
   Blocked by: `appforge-poc#5` and `ditc_terraform#8`  
   Why fourth: defines canonical queries used by dashboard + runbook.

5. **Define ACE operations dashboard specification from lifecycle queries**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/10  
   Blocked by: `appforge-poc#9`  
   Why fifth: builds visual monitoring from the canonical query layer.

6. **Publish ACE observability troubleshooting runbook and validation flow**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/11  
   Blocked by: `appforge-poc#9` and `appforge-poc#10`  
   Why sixth: finalizes operator procedures after artifacts are in place.

7. **Umbrella outcome: end-to-end dashboard + runbook delivery**  
   Repo: `Day-in-the-Country-LLC/appforge-poc`  
   Issue: https://github.com/Day-in-the-Country-LLC/appforge-poc/issues/6  
   Blocked by: `appforge-poc#9`, `appforge-poc#10`, `appforge-poc#11`  
   Why last: rollup acceptance for the full observability package.

## Dependency Chain Summary

`appforge-poc#4` -> `appforge-poc#5`  
`appforge-poc#4` -> `ditc_terraform#8`  
`appforge-poc#5` + `ditc_terraform#8` -> `appforge-poc#9`  
`appforge-poc#9` -> `appforge-poc#10`  
`appforge-poc#9` + `appforge-poc#10` -> `appforge-poc#11`  
`appforge-poc#9` + `appforge-poc#10` + `appforge-poc#11` -> `appforge-poc#6`
