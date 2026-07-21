# Extra certificates for offline / Server Core VS install

Layout `certificates\` does **not** include everything modern VS bootstrappers need.

Required extra (commonly causes exit **5003** / `InvalidCertificate` on `vs_installer.opc`):

- `Microsoft Windows Code Signing PCA 2024.crt`  
  https://www.microsoft.com/pkiops/certs/Microsoft%20Windows%20Code%20Signing%20PCA%202024.crt

This file is also copied under `scripts/certs/` so `COPY scripts` in the factory Dockerfile picks it up automatically.
