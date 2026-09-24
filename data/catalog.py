from dataclasses import dataclass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    category: str
    price_inr: int


PRODUCTS = [
    Product("AUD-OVR-01", "Aurel Over Ear Wireless Headphones", "headphones_over_ear", 2400),
    Product("AUD-OVR-02", "Aurel Studio ANC Headphones", "headphones_over_ear", 7999),
    Product("AUD-INE-01", "Nimbus Wired Earphones", "earphones_in_ear", 599),
    Product("AUD-INE-02", "Nimbus Buds TWS Earbuds", "earphones_in_ear", 2999),
    Product("AUD-SPK-01", "Kora Mini Bluetooth Speaker", "speaker", 3000),
    Product("AUD-SPK-02", "Kora Party Speaker", "speaker", 4499),
    Product("AUD-SBR-01", "Kora 2.1 Soundbar", "speaker", 5049),
    Product("PHN-01", "Zephyr 5G Phone 128GB", "phone", 18999),
    Product("PHN-02", "Zephyr Pro Phone 256GB", "phone", 32999),
    Product("LAP-01", "Veda 14 Laptop Core i5", "laptop", 44990),
    Product("LAP-02", "Veda Chromebook 11", "laptop", 24999),
    Product("TAB-01", "Zephyr Tab 10", "tablet", 15499),
    Product("WAT-01", "Kora Fit Smartwatch", "smartwatch", 3499),
    Product("WAT-02", "Kora Fit Band", "smartwatch", 1799),
    Product("KIT-01", "Steel Water Bottle 1L", "kitchen", 450),
    Product("KIT-02", "Non Stick Cookware Set", "kitchen", 2890),
    Product("KIT-03", "Ceramic Dinner Set 18 Pieces", "kitchen", 1899),
    Product("APL-01", "Mixer Grinder 750W", "appliance", 3049),
    Product("APL-02", "Air Fryer 4L", "appliance", 5000),
    Product("APL-03", "Microwave Oven 25L", "appliance", 10000),
    Product("APL-04", "Robot Vacuum Cleaner", "appliance", 12999),
    Product("DEC-01", "Glass Table Lamp", "home_decor", 1250),
    Product("FUR-01", "Ergonomic Office Chair", "furniture", 7500),
    Product("FAS-01", "Cotton Kurta", "fashion", 899),
    Product("FAS-02", "Denim Jacket", "fashion", 1999),
    Product("FTW-01", "Running Shoes", "footwear", 2799),
    Product("ACC-01", "Leather Wallet", "accessories", 749),
    Product("INN-01", "Cotton Innerwear Pack of 3", "innerwear", 499),
    Product("PER-01", "Beard Trimmer", "personal_care", 1299),
    Product("PER-02", "Hair Dryer 1600W", "personal_care", 1599),
    Product("BOK-01", "Paperback Novel", "books", 350),
    Product("BOK-02", "Exam Prep Book Set", "books", 1150),
    Product("STA-01", "Pocket Notebook", "stationery", 150),
    Product("TOY-01", "Building Blocks Set", "toys", 1499),
    Product("GFT-01", "Gift Card Rs 1000", "gift_card", 1000),
    Product("GRO-01", "Assorted Dry Fruits 1kg", "grocery", 1150),
    Product("CUS-01", "Personalised Photo Mug", "customised", 399),
    Product("TVS-01", "Smart TV 32 inch", "tv", 14999),
    Product("TVS-02", "Smart TV 50 inch", "tv", 36999),
    Product("CAM-01", "Action Camera 4K", "camera", 25000),
]

CATALOG = {p.sku: p for p in PRODUCTS}
