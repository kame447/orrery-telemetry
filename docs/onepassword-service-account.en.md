# Unattended 1Password access for DeepSeek on macOS

The desktop integration requires periodic biometric authorization. A dedicated Service Account provides unattended reads without storing the DeepSeek API key in a local environment file.

## One-time operator preparation

First create a separate 1Password vault named, for example, Orrery API. Move only the DeepSeek API credential to this vault. Service Accounts cannot access the built-in Personal, Private, Employee, or default Shared vaults.

Create a Service Account on 1Password.com, with read-item access restricted to this single vault. Do not grant write, deletion, share, or vault-management privileges. The resulting token is displayed only once. Save it securely.

Open macOS Keychain Access and add a generic password to the login keychain. Use service/item name orrery-1password-service-account, account deepseek, and place the Service Account token in its password field. This is not the DeepSeek API key; do not send either secret through chat, Git, logs, or shell history.

After moving the DeepSeek item, copy its new op:// vault/item/field reference. Configure the private manifest credential_ref with these metadata fields: store set to 1password-service-account, reference set to that op:// URI, and keychain set to an object containing service and account matching the newly created Keychain entry. No secret value belongs in JSON.

Validate both native harnesses, verify the reader after the 1Password desktop app has locked, and confirm an actual DeepSeek response before activating the new manifest. Activation can be done with a targeted Dashboard restart while retaining the running Mail service.

## Security and limitations

The reader obtains the Service Account token only from macOS Keychain, passes it to the op CLI in the child process's local OP_SERVICE_ACCOUNT_TOKEN environment, then returns the selected DeepSeek credential in memory to the selected harness. A failed token lookup does not fall back to personal desktop app access or an ambient token.

The Service Account is itself a long-lived credential. Limiting its scope to a single vault reduces but does not remove that risk. Never export its token globally. This mode requires an accessible login Keychain and does not guarantee operation before the Mac user logs in. Revocation, expired tokens, missing permissions, and offline 1Password access remain failure conditions.

Official references:
https://www.1password.dev/service-accounts
https://www.1password.dev/service-accounts/get-started
https://www.1password.dev/service-accounts/use-with-1password-cli
