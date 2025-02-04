from base64 import b64encode, b64decode

from pywidevine.device import Device, DeviceTypes

def create_device_wvd(client_id_file, private_key_file, output_file="Device.wvd"):
    try:
        with open(client_id_file, "rb") as cid_file:
            cid = cid_file.read()

        with open(private_key_file, "rb") as prk_file:
            prk = prk_file.read()

        device = Device(
            client_id=cid,
            private_key=prk,
            type_=DeviceTypes['ANDROID'],
            security_level=3,
            flags=None
        ).dumps()

        with open(output_file, "wb") as output:
            output.write(device)

        print(f"Fichier {output_file} créé avec succès.")

    except Exception as e:
        print(f"Une erreur est survenue : {e}")

create_device_wvd("client_id.bin", "private_key.pem")
