import os

path = r'\\mgsvr03\catalog'
print(f"Checking access to: {path}")

if os.path.exists(path):
    print("Success: Python can see the network folder!")
    try:
        files = os.listdir(path)
        print(f"Success: Found {len(files)} items in the root folder.")
    except Exception as e:
        print(f"Failed: Can see the folder, but cannot read contents. Error: {e}")
else:
    print("Failed: Python cannot see the network path at all. Check permissions/VPN.")