# Job DSL seed (optional)

Use in a Jenkins seed job to create pipeline jobs from this repo.

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

Required Jenkins credentials:
- `portal-base-url` (secret text)
- `portal-callback-hmac` (secret text)

Required node label:
- `msbuild-factory` — dedicated Windows factory host/VM
