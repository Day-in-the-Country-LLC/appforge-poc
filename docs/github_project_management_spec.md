This document outlines all of the necessary steps to manage a GitHub project using the ACE framework.

Issue creation:
- requires several considerations to work properly with project architectures and ACE's issue management system
- First, every repo has a project_architecture.md file that outlines the project architecture and what project repos handle what types of issues (e.g. infrastructure in terraform, project orchestration layers, agent tools, frontend coding, etc.).  Before creating an issue for the project, this needs to be consulted to understand which repo the issue should be created in.
- Second, issues should be created in a discrete and modular fashion.  Each issue should be focused on a single task or goal.  This makes it easier to track progress and manage the project.
- Third, issue dependencies should be carefully considered.  If an issue depends on another issue, this should be clearly outlined in the issue description.  This helps to ensure that issues are completed in the correct order. When the issue is created in github, issue relationships should be used to outline these dependencies.
- Fourth, the agent creating the issues should determine whether any of the issues need to be handled by the human developer (e.g. update DNS setting for domain in GoDaddy), by a local agent (local machine files are required to complete the task that will not be available on the remote VM), or by a remote agent (the task can be completed autonomously by the agent on the remote vm).  These should be indicated by a label in the issue: 'developer', 'agent:local', 'agent:remote' respectively.
- Fifth, the agent creating the issues should use a label for each issue to specify the level of difficulty required to complete the task. There are three difficulty labels: 'difficulty:easy', 'difficulty:medium', 'difficulty:hard'. These map to the coding model configured in settings (DIFFICULTY_*). Always ensure the appropriate difficulty label is specified in every issue.
- Sixth, all new issues should be created (github-mcp) with the 'Backlog' status for review by the human developer.  (appforge-mcp)

Repo issue commands can be executed using the github-mcp server.  All project board commands (issue status) will require the use of the appforge-mcp server.
