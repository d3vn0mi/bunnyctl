

1. Change USB specifics and confirm 



.
python3 Bunnyctl.py ids usb_sandisk --sn "4C530001071112116351"

python3 bunnyctl.py ids kbd1 --vid 0x413c --pid 0x2113 --man "Dell" --prod "KB216 Wired Keyboard"

python3 bunnyctl.py ids usb_sandisk          #

mode usb_sandisk -> SanDisk storage 
mode kbd1 -> payload one
mode kbd2 -> payload two

mode2 kbd1 -> payload one
mode2 kbd2 -> payload two
mode2 usb_sandisk -> SanDisk storage


attempt with python3 bunnyctl.py mode2 kbd1  didnt change the value


payload two
