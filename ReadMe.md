Ce script Python minimaliste, simple et efficace permet de télécharger des animés depuis Crunchyroll. Il a été fait durant le 06/2024, et est toujours fonctionnel.

## Prérequis

Commencez par installer ces packages :

```bash
pip install curl-cffi customtkinter pywidevine rich tqdm
```

Crunchyroll utilise le niveau de sécurité Widevine L3 / Playready SL2000 pour la protection de son contenu.<br>
Pour des raisons légales, le CDM n'est pas inclus dans ce script. Vous devez vous le procurer indépendamment.<br>
Si vous possédez les 2 fichiers `device_client_id_blob.bin` (ID client) et `device_private_key.pem` (clé privée), vous pouvez utiliser le script **CreateWVD.py** pour générer un fichier au format WVD (Widevine Device).<br>
ffmpeg doit être accessible via votre variable d'environnement PATH.

## Utilisation

<ol>
<li>D'abord, collez votre 'Access Token'.</li>
<li>Collez l'URL d'un épisode.</li>
<li>Choisissez la langue audio souhaitée parmi les options disponibles en entrant le numéro correspondant.</li>
<li>Une fenêtre s'ouvre, cliquez sur les épisodes que vous voulez.</li>
<div style="text-align: center;">
<img src="./Captures/Capture 1.png">
</div>
<li>Choisissez la langue des sous-titres ou sélectionnez "Aucun sous-titres" si vous ne souhaitez pas de sous-titres en entrant le numéro correspondant.</li>
<li>Ce script est conçu pour sélectionner et télécharger automatiquement la meilleure qualité vidéo et audio disponibles.</li>
</ol>
