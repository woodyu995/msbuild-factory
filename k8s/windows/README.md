# Kubernetes Windows worker pools for MSBuild project builds

## Node pools

| Pool | OS | Labels | Taint |
|------|----|--------|-------|
| msbuild-2019 | Windows Server 2019 | `kubernetes.io/os=windows`, `build.company.io/windows-release=ltsc2019`, `build.company.io/purpose=msbuild` | `build.company.io/windows=true:NoSchedule` |
| msbuild-2022 | Windows Server 2022 | same with `ltsc2022` | same |

## Templates

- `pod-templates/msbuild-builder-ltsc2019.yaml`
- `pod-templates/msbuild-builder-ltsc2022.yaml`

Runtime rendering (preferred):

```http
GET /api/v1/build-requests/{id}/pod-template
```

Portal fills image digest + windowsBase from the resolved request.

## Checklist

- [ ] Internal registry pull secret `internal-registry-secret`
- [ ] Hot Preset images pre-pulled on nodes
- [ ] ephemeral-storage monitoring (alarm at 80%)
- [ ] LTSC image ↔ node release match enforced by nodeSelector
