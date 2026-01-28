Appforge Issue Management

There are two levels of issue management in appforge:
1) Manager Agent Issue Management - The manager needs to find 
    a) issues that either need to be started
        - Ready status
        - label -> agent:remote
        - no blocking issues in relationships that do not have status Done
    b) issues that need to be resumed.  
        - In Progress status
        - label -> agent:remote
        - not currently claimed by another agent

2) CLI Coding Agent Issue Management - change status for
    a) issues that need developer input
        - change status to Blocked
        - assign to repo-owner
        - comment with details about what needs to be done
        - unclaim issue so when developer finishes, they can put it back in In progress to be picked up again by another agent
    b) issues that are ready for review
        - In review status
        - create PR with
            * summary of work completed
            * any suggestions for how to test
        - comment in issue with link to PR
        - assign to repo-owner
        - optional: send a notification via Twilio with link to the PR and a brief summary


# Need a whole system for handling PR comments - there's a skill but it needs to be reviewed and refined.
