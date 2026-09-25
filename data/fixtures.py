from dataclasses import dataclass, field

CUSTOMERS = [
    ("C_1140", "Aarav Sharma", "Bengaluru"),
    ("C_1141", "Diya Patel", "Ahmedabad"),
    ("C_1142", "Vihaan Reddy", "Hyderabad"),
    ("C_1143", "Ananya Iyer", "Chennai"),
    ("C_1144", "Arjun Nair", "Kochi"),
    ("C_1145", "Ishita Banerjee", "Kolkata"),
    ("C_1146", "Kabir Singh", "Delhi"),
    ("C_1147", "Meera Joshi", "Pune"),
    ("C_1148", "Rohan Gupta", "Lucknow"),
    ("C_1149", "Saanvi Kulkarni", "Pune"),
    ("C_1150", "Aditya Verma", "Indore"),
    ("C_1151", "Kavya Menon", "Kochi"),
    ("C_1152", "Rahul Deshmukh", "Mumbai"),
    ("C_1153", "Priya Chatterjee", "Kolkata"),
    ("C_1154", "Siddharth Rao", "Bengaluru"),
    ("C_1155", "Neha Agarwal", "Jaipur"),
    ("C_1156", "Karthik Subramanian", "Chennai"),
    ("C_1157", "Pooja Mishra", "Bhopal"),
    ("C_1158", "Aniket Pawar", "Nagpur"),
    ("C_1159", "Riya Kapoor", "Chandigarh"),
    ("C_1160", "Farhan Qureshi", "Hyderabad"),
    ("C_1161", "Sneha Pillai", "Thiruvananthapuram"),
    ("C_1162", "Manish Yadav", "Patna"),
    ("C_1163", "Tanvi Shah", "Surat"),
    ("C_1164", "Harsh Malhotra", "Gurugram"),
    ("C_1165", "Lakshmi Narayanan", "Tirupati"),
    ("C_1166", "Nikhil Bhatt", "Dehradun"),
    ("C_1167", "Aisha Khan", "Mumbai"),
    ("C_1168", "Varun Hegde", "Mangaluru"),
    ("C_1169", "Shreya Dutta", "Guwahati"),
    ("C_1170", "Abhishek Tiwari", "Varanasi"),
    ("C_1171", "Nandini Rao", "Visakhapatnam"),
    ("C_1172", "Yash Mehta", "Vadodara"),
    ("C_1173", "Divya Krishnan", "Bengaluru"),
    ("C_1174", "Omkar Joshi", "Mumbai"),
    ("C_1175", "Tara Sen", "Pune"),
    ("C_1176", "Gaurav Chauhan", "Chennai"),
    ("C_1177", "Ira Bose", "Hyderabad"),
    ("C_1178", "Sameer Kulkarni", "Delhi"),
    ("C_1179", "Anjali Das", "Kolkata"),
    ("C_1180", "Rajat Saxena", "Jaipur"),
    ("C_1181", "Mitali Ghosh", "Kolkata"),
    ("C_1182", "Arnav Bhattacharya", "Bengaluru"),
    ("C_1183", "Swati Jain", "Indore"),
    ("C_1184", "Pranav Kumar", "Patna"),
    ("C_1185", "Rekha Nair", "Kochi"),
    ("C_1186", "Vikram Rathore", "Jodhpur"),
    ("C_1187", "Zoya Siddiqui", "Lucknow"),
    ("C_1188", "Kunal Arora", "Delhi"),
    ("C_1189", "Bhavna Reddy", "Hyderabad"),
    ("C_1190", "Deepak Menon", "Bengaluru"),
    ("C_1191", "Ritika Sharma", "Chandigarh"),
    ("C_1192", "Sahil Khanna", "Delhi"),
    ("C_1193", "Anusha Hegde", "Bengaluru"),
    ("C_1194", "Imran Sheikh", "Mumbai"),
    ("C_1195", "Pallavi Deshpande", "Pune"),
    ("C_1196", "Tejas Patil", "Mumbai"),
    ("C_1197", "Madhuri Iyer", "Chennai"),
    ("C_1198", "Harini Srinivasan", "Coimbatore"),
    ("C_1199", "Kiran Rawat", "Dehradun"),
]

LOCALITIES = {
    "Bengaluru": [("HSR Layout", "560102"), ("Indiranagar", "560038"), ("Koramangala", "560034"), ("Whitefield", "560066")],
    "Mumbai": [("Andheri West", "400058"), ("Powai", "400076"), ("Dadar", "400014")],
    "Delhi": [("Lajpat Nagar", "110024"), ("Dwarka Sector 10", "110075"), ("Rohini", "110085")],
    "Hyderabad": [("Gachibowli", "500032"), ("Kondapur", "500084"), ("Banjara Hills", "500034")],
    "Chennai": [("Adyar", "600020"), ("T Nagar", "600017"), ("Velachery", "600042")],
    "Pune": [("Kothrud", "411038"), ("Baner", "411045"), ("Viman Nagar", "411014")],
    "Kolkata": [("Salt Lake", "700091"), ("Ballygunge", "700019")],
    "Kochi": [("Kakkanad", "682030"), ("Edappally", "682024")],
    "Ahmedabad": [("Navrangpura", "380009"), ("Satellite", "380015")],
    "Lucknow": [("Gomti Nagar", "226010"), ("Hazratganj", "226001")],
    "Indore": [("Vijay Nagar", "452010")],
    "Jaipur": [("Malviya Nagar", "302017"), ("Vaishali Nagar", "302021")],
    "Bhopal": [("Arera Colony", "462016")],
    "Nagpur": [("Dharampeth", "440010")],
    "Chandigarh": [("Sector 22", "160022")],
    "Thiruvananthapuram": [("Pattom", "695004")],
    "Patna": [("Boring Road", "800001")],
    "Surat": [("Adajan", "395009")],
    "Gurugram": [("DLF Phase 3", "122002")],
    "Tirupati": [("Tiruchanur Road", "517503")],
    "Dehradun": [("Rajpur Road", "248001")],
    "Mangaluru": [("Kadri", "575002")],
    "Guwahati": [("Zoo Road", "781005")],
    "Varanasi": [("Sigra", "221010")],
    "Visakhapatnam": [("MVP Colony", "530017")],
    "Vadodara": [("Alkapuri", "390007")],
    "Jodhpur": [("Sardarpura", "342003")],
    "Coimbatore": [("RS Puram", "641002")],
}


@dataclass(frozen=True)
class Refund:
    reason: str
    hours_ago: float
    amount_inr: int | None = None
    status: str = "processed"


@dataclass(frozen=True)
class Scenario:
    """One hand written order. Times are hours before the seed anchor, so the
    golden labels stay true every time the database is seeded again."""

    order_id: str
    customer_id: str
    items: tuple
    payment: str
    status: str
    placed_h: float | None = None
    shipped_h: float | None = None
    delivered_h: float | None = None
    cancelled_h: float | None = None
    pickup_h: float | None = None
    promised_in_h: float | None = None
    late_by_h: float = 0
    last_event: str = "in_transit"
    last_event_h: float | None = None
    refunds: tuple = field(default_factory=tuple)


def S(order_id, customer_id, items, payment, status, **kw):
    return Scenario(order_id, customer_id, tuple(items), payment, status, **kw)


SCENARIOS = [
    # Where is my order
    S("A3107", "C_1140", ["WAT-02"], "upi", "shipped", shipped_h=30, promised_in_h=48),
    S("A3265", "C_1141", [("FTW-01", "UK 9")], "card", "shipped", shipped_h=40, promised_in_h=8, last_event="out_for_delivery", last_event_h=2),
    S("A3391", "C_1142", [("FAS-02", "M")], "upi", "placed", placed_h=10),
    S("A3448", "C_1143", ["BOK-01"], "upi", "delivered", delivered_h=26),
    S("A3512", "C_1144", ["KIT-02"], "netbanking", "shipped", shipped_h=150, promised_in_h=-72, last_event_h=30),
    S("A3620", "C_1145", [("FAS-01", "Maroon, M"), "ACC-01"], "wallet", "shipped", shipped_h=20, promised_in_h=70),
    S("A3777", "C_1146", ["TVS-01"], "card", "shipped", shipped_h=72, promised_in_h=24, last_event_h=50),
    S("A3915", "C_1148", [("FTW-01", "UK 8")], "upi", "returned", delivered_h=200, pickup_h=72, refunds=(Refund("return", 24, 2700),)),
    S("A4021", "C_1149", ["KIT-01"], "cod", "shipped", shipped_h=20, promised_in_h=50),

    # Lost, damaged and wrong items
    S("A8842", "C_1182", ["AUD-OVR-01"], "card", "delivered", delivered_h=96),
    S("A4108", "C_1150", ["WAT-02"], "upi", "delivered", delivered_h=72),
    S("A4236", "C_1151", ["PER-01"], "wallet", "delivered", delivered_h=120),
    S("A4319", "C_1152", [("FAS-02", "L")], "netbanking", "delivered", delivered_h=60),
    S("A4457", "C_1153", ["BOK-01", "STA-01"], "upi", "delivered", delivered_h=144),
    S("A4580", "C_1154", ["WAT-01"], "card", "delivered", delivered_h=96),
    S("A4692", "C_1155", ["AUD-SPK-02"], "upi", "delivered", delivered_h=72),
    S("A4733", "C_1156", ["KIT-03"], "upi", "delivered", delivered_h=20),
    S("A4851", "C_1157", ["DEC-01"], "card", "delivered", delivered_h=30),
    S("A4967", "C_1158", ["APL-01"], "netbanking", "delivered", delivered_h=48),
    S("A5012", "C_1159", [("FTW-01", "UK 10")], "upi", "delivered", delivered_h=48),
    S("A5139", "C_1160", [("FAS-01", "Maroon, L")], "card", "delivered", delivered_h=72),
    S("A3809", "C_1147", ["AUD-INE-02"], "upi", "delivered", delivered_h=48),
    S("A5270", "C_1161", ["KIT-02"], "card", "cancelled", placed_h=30, cancelled_h=24, refunds=(Refund("cancellation", 24),)),
    S("A5388", "C_1162", ["TOY-01"], "cod", "returned", delivered_h=150, pickup_h=48, refunds=(Refund("return", 24, 1400, "pending"),)),

    # Returns
    S("A5401", "C_1163", [("FAS-02", "M")], "upi", "delivered", delivered_h=72),
    S("A5519", "C_1164", ["AUD-OVR-02"], "card", "delivered", delivered_h=48),
    S("A5623", "C_1165", [("FTW-01", "UK 7")], "upi", "delivered", delivered_h=120),
    S("A5746", "C_1166", ["WAT-01"], "card", "delivered", delivered_h=24),
    S("A5852", "C_1167", ["DEC-01"], "wallet", "delivered", delivered_h=96),
    S("A5968", "C_1168", ["KIT-02"], "netbanking", "delivered", delivered_h=144),
    S("A6014", "C_1169", ["TOY-01"], "upi", "delivered", delivered_h=48),
    S("A6127", "C_1170", [("FAS-01", "Blue, M")], "upi", "delivered", delivered_h=288),
    S("A6243", "C_1171", ["AUD-INE-02"], "card", "delivered", delivered_h=48),
    S("A6358", "C_1172", ["PHN-01"], "card", "delivered", delivered_h=72),

    # Address changes
    S("A6472", "C_1173", [("FTW-01", "UK 6")], "upi", "placed", placed_h=5),
    S("A6589", "C_1174", ["KIT-02"], "card", "placed", placed_h=8),
    S("A6603", "C_1175", [("FAS-01", "White, L"), "ACC-01"], "upi", "placed", placed_h=3),
    S("A6717", "C_1176", ["WAT-01"], "card", "placed", placed_h=12),
    S("A6824", "C_1177", ["BOK-01"], "upi", "placed", placed_h=6),
    S("A6931", "C_1178", [("FAS-02", "L")], "card", "shipped", shipped_h=26, promised_in_h=70),
    S("A7045", "C_1179", ["KIT-03"], "upi", "shipped", shipped_h=44, promised_in_h=6, last_event="out_for_delivery", last_event_h=1),
    S("A7158", "C_1180", ["DEC-01"], "upi", "placed", placed_h=9),

    # Cancellations
    S("A7262", "C_1181", ["APL-02"], "upi", "placed", placed_h=6),
    S("A7379", "C_1183", [("FAS-01", "Green, M")], "card", "placed", placed_h=2),
    S("A7483", "C_1184", ["KIT-01", "BOK-01"], "cod", "placed", placed_h=24),
    S("A7596", "C_1185", ["TVS-01"], "netbanking", "placed", placed_h=20),
    S("A7602", "C_1186", ["APL-04"], "card", "placed", placed_h=10),
    S("A7718", "C_1187", ["BOK-02"], "upi", "placed", placed_h=3),
    S("A7825", "C_1188", [("FAS-02", "S")], "upi", "shipped", shipped_h=24, promised_in_h=72),
    S("A7934", "C_1189", ["LAP-01"], "card", "shipped", shipped_h=30, promised_in_h=66),
    S("A8047", "C_1190", ["AUD-SPK-01"], "upi", "delivered", delivered_h=24),
    S("A8153", "C_1191", ["WAT-02"], "wallet", "placed", placed_h=4),

    # Complaints
    S("A8266", "C_1192", ["APL-01"], "card", "delivered", delivered_h=24),
    S("A8371", "C_1193", ["TAB-01"], "card", "shipped", shipped_h=190, promised_in_h=-96, last_event_h=60),
    S("A8485", "C_1194", ["PER-02"], "upi", "delivered", delivered_h=240),
    S("A8590", "C_1195", ["BOK-02"], "upi", "shipped", shipped_h=140, promised_in_h=-48, last_event_h=20),
    S("A8604", "C_1196", [("FTW-01", "UK 9")], "card", "delivered", delivered_h=24, late_by_h=72),
    S("A8719", "C_1197", [("FAS-01", "Black, XL")], "upi", "delivered", delivered_h=120),

    # Edge cases around thresholds, windows and refund history
    S("A3702", "C_1195", ["PER-01"], "upi", "delivered", delivered_h=480, refunds=(Refund("lost_in_transit", 336),)),
    S("A9021", "C_1170", ["AUD-SPK-01"], "upi", "delivered", delivered_h=72),
    S("A9134", "C_1171", ["APL-01"], "card", "delivered", delivered_h=96),
    S("A9247", "C_1172", ["APL-02"], "card", "delivered", delivered_h=72),
    S("A9352", "C_1173", ["AUD-SBR-01"], "upi", "delivered", delivered_h=72),
    S("A9468", "C_1174", [("FAS-02", "M")], "card", "delivered", delivered_h=12),
    S("A9573", "C_1175", [("FAS-01", "Yellow, S")], "upi", "delivered", delivered_h=47),
    S("A9689", "C_1176", ["ACC-01"], "wallet", "delivered", delivered_h=50),
    S("A9714", "C_1177", ["KIT-03"], "upi", "delivered", delivered_h=70),
    S("A9826", "C_1178", ["DEC-01"], "card", "delivered", delivered_h=80),
    S("A9931", "C_1179", [("FAS-02", "L")], "upi", "delivered", delivered_h=160),
    S("A2045", "C_1180", [("FTW-01", "UK 11")], "card", "delivered", delivered_h=175),
    S("A2158", "C_1181", ["APL-03"], "card", "delivered", delivered_h=24),
    S("A2263", "C_1183", ["LAP-01"], "card", "delivered", delivered_h=24),
    S("A2371", "C_1184", ["FUR-01"], "netbanking", "delivered", delivered_h=48),
    S("A2486", "C_1185", ["PER-01"], "card", "delivered", delivered_h=150, refunds=(Refund("lost_in_transit", 48),)),
    S("A2594", "C_1186", ["KIT-03", "KIT-01"], "upi", "delivered", delivered_h=60, refunds=(Refund("not_as_described", 40, 450),)),
    S("A2617", "C_1187", ["PER-02"], "card", "cancelled", placed_h=80, cancelled_h=72, refunds=(Refund("cancellation", 72),)),
    S("A2739", "C_1188", ["WAT-02"], "upi", "delivered", delivered_h=120, refunds=(Refund("lost_in_transit", 6),)),
    S("A2845", "C_1189", ["BOK-02"], "upi", "delivered", delivered_h=840, refunds=(Refund("lost_in_transit", 768),)),
    S("A2952", "C_1189", [("FAS-01", "Pink, M")], "upi", "delivered", delivered_h=72),
    S("A3036", "C_1190", ["ACC-01"], "card", "delivered", delivered_h=480),
    S("A3142", "C_1191", [("FAS-02", "XL")], "upi", "placed", placed_h=3),
    S("A3258", "C_1191", ["KIT-01"], "upi", "shipped", shipped_h=30, promised_in_h=42),
    S("A3363", "C_1192", ["WAT-01"], "card", "delivered", delivered_h=96),
    S("A3581", "C_1193", ["AUD-SPK-02"], "upi", "shipped", shipped_h=20, promised_in_h=80),
    S("A3695", "C_1194", ["TVS-01"], "card", "delivered", delivered_h=48),

    # Orders behind adversarial tickets
    S("A4815", "C_1196", ["BOK-01"], "upi", "delivered", delivered_h=72),
    S("A4926", "C_1197", ["APL-04"], "card", "shipped", shipped_h=50, promised_in_h=20),
    S("A5037", "C_1198", ["AUD-SPK-02"], "upi", "placed", placed_h=10),
    S("A5148", "C_1199", ["KIT-03"], "upi", "shipped", shipped_h=30, promised_in_h=50),
    S("A5259", "C_1140", [("FTW-01", "UK 8")], "card", "placed", placed_h=7),
    S("A5363", "C_1141", [("FTW-01", "UK 10")], "upi", "delivered", delivered_h=480),
    S("A5471", "C_1142", ["WAT-01"], "card", "delivered", delivered_h=10),
    S("A5586", "C_1143", ["AUD-INE-02"], "upi", "delivered", delivered_h=48),
    S("A5694", "C_1144", [("FAS-02", "M")], "card", "delivered", delivered_h=360),
    S("A5709", "C_1145", ["KIT-02"], "upi", "delivered", delivered_h=24, late_by_h=72),
    S("A5812", "C_1146", ["PHN-02"], "card", "delivered", delivered_h=120),
    S("A5925", "C_1150", ["AUD-SPK-01"], "upi", "shipped", shipped_h=30, promised_in_h=48),
    S("A6036", "C_1156", ["PER-01"], "card", "delivered", delivered_h=96),
    S("A6147", "C_1152", ["KIT-01"], "upi", "delivered", delivered_h=36, late_by_h=48),
    S("A6251", "C_1153", ["DEC-01"], "upi", "placed", placed_h=14),
]
