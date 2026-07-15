# Jenkins jobs, agents, and wiring for MSBuild image factory

## Layout

```text
jenkins/
  casc/           # Configuration as Code examples
  jobs/           # Pipeline Jenkinsfiles
  shared-library/ # Portal agent CLIs
  WIRING.md       # Cutover checklist
```

## Jobs

| Job | Purpose | Agent |
|-----|---------|-------|
| `msbuild-image-factory` | Build/push profile images | `msbuild-factory` Windows node |
| `msbuild-project-build` | Build solution on READY digest | K8s Windows pod |

Seed snippet: see below. Full wiring: [WIRING.md](./WIRING.md).

```groovy
pipelineJob('msbuild-image-factory') {
  parameters {
    stringParam('BUILD_REQUEST_ID')
    stringParam('PROFILE_HASH')
    stringParam('FACTORY_LEASE_ID')
  }
  definition {
    cpsScm {
      scm {
        git {
          remote { url('https://git.internal/msbuild-factory.git') }
          branches('*/main')
        }
      }
      scriptPath('jenkins/jobs/msbuild-image-factory/Jenkinsfile')
    }
  }
}

pipelineJob('msbuild-project-build') {
  parameters {
    stringParam('BUILD_REQUEST_ID')
  }
  definition {
    cpsScm {
      scm {
        git {
          remote { url('https://git.internal/msbuild-factory.git') }
          branches('*/main')
        }
      }
      scriptPath('jenkins/jobs/msbuild-project-build/Jenkinsfile')
    }
  }
}
```

## Credentials

- `portal-base-url`
- `portal-callback-hmac`
- `portal-jenkins-api` (Portal → Jenkins trigger)

## Agents

- Portal factory agent: `shared-library/scripts/portal_factory_agent.py`
- Portal project agent: `shared-library/scripts/portal_project_agent.py`
  - `resolve`, `pod-template`, `event`
