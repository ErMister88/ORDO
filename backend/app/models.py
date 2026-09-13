"""Pydantic request/response models."""
from pydantic import BaseModel
from typing import List, Optional, Literal

from .core import Role


class PublicUser(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    companyId: Optional[str] = None
    salesRepId: Optional[str] = None
    must_change_password: bool = False


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


class OfferItemIn(BaseModel):
    productId: str
    qty: float
    price: float


class OfferCreate(BaseModel):
    companyId: str
    items: List[OfferItemIn]
    termMonths: int = 48
    reason: Optional[str] = ""


class OrderItemIn(BaseModel):
    productId: str
    qty: float


class OrderCreate(BaseModel):
    companyId: str
    items: List[OrderItemIn]


class DecisionIn(BaseModel):
    note: Optional[str] = ""


class DiscountTier(BaseModel):
    minQty: float
    price: float


class ProductIn(BaseModel):
    brand: str
    name: str
    unit: str = "kg"
    standardPrice: float
    salesFloor: float
    absoluteFloor: float
    cost: float
    description: str = ""
    imageUrl: str = ""
    discountTiers: List[DiscountTier] = []
    taxRate: int = 7
    stock: Optional[float] = None
    b2cPrice: Optional[float] = None
    active: bool = True


class CustomerPriceIn(BaseModel):
    companyId: str
    productId: str
    price: float


class OrderStatusIn(BaseModel):
    status: str


class ActiveIn(BaseModel):
    active: bool


class StockIn(BaseModel):
    stock: Optional[float] = None


class ShopSettingsIn(BaseModel):
    freeShippingThreshold: float = 50.0
    shippingFee: float = 4.90
    newsletterDiscountPercent: int = 10
    newsletterDiscountEnabled: bool = True


class ShopCustomerIn(BaseModel):
    name: str
    email: str
    phone: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""


class ShopItemIn(BaseModel):
    productId: str
    qty: float


class ShopOrderIn(BaseModel):
    items: List[ShopItemIn]
    customer: ShopCustomerIn
    promoCode: Optional[str] = None


class NewsletterIn(BaseModel):
    email: str
    name: Optional[str] = ""
    baseUrl: Optional[str] = None


class ValidateCodeIn(BaseModel):
    code: str


class PushBroadcastIn(BaseModel):
    title: str
    message: str
    actionUrl: Optional[str] = None


class ShopStatusIn(BaseModel):
    status: str
    trackingNumber: Optional[str] = None


class ShopAddressIn(BaseModel):
    name: str = ""
    phone: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""


class MachineIn(BaseModel):
    name: str
    description: str = ""
    imageUrl: str = ""
    price: float  # Bruttopreis inkl. 19% MwSt (Kauf)
    active: bool = True


class MachineRequestIn(BaseModel):
    machineId: str
    type: str  # "kauf" | "finanzierung" | "leasing"
    termMonths: Optional[int] = 48
    message: str = ""


class MachineTermsIn(BaseModel):
    status: Optional[str] = None
    downPayment: Optional[float] = None
    monthlyRate: Optional[float] = None
    finalPayment: Optional[float] = None
    termMonths: Optional[int] = None
    minCoffeeKgMonth: Optional[float] = None
    productId: Optional[str] = None
    coffeePricePerKg: Optional[float] = None
    note: str = ""


class MachineRespondIn(BaseModel):
    action: str  # "decline" | "question"
    message: str = ""


class ShopRegisterIn(BaseModel):
    name: str
    email: str
    password: str


class ShopLoginIn(BaseModel):
    email: str
    password: str


class NewCompanyIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""


class CompanyUpdateIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""
    vatId: str = ""
    assignedSalesRepId: Optional[str] = None
    orderCycleDays: int = 30
    active: bool = True


class CreateUserIn(BaseModel):
    name: str
    email: str
    role: Literal["sales", "customer"]
    companyId: Optional[str] = None
    newCompany: Optional[NewCompanyIn] = None


class ForgotPwIn(BaseModel):
    email: str


class ResetPwIn(BaseModel):
    email: str
    code: str
    newPassword: str


class ChangePwIn(BaseModel):
    currentPassword: str
    newPassword: str


class AcceptOfferIn(BaseModel):
    note: Optional[str] = ""


class SubscriptionIn(BaseModel):
    companyId: str
    items: List[OfferItemIn]
    intervalDays: int = 28


class CollectiveInvoiceIn(BaseModel):
    companyId: str
    year: int
    month: int
