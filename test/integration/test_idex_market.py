#!/usr/bin/env python
from os.path import join, realpath
import sys; sys.path.insert(0, realpath(join(__file__, "../../../")))

import asyncio
import conf
import contextlib
from decimal import Decimal
import logging
import time
from typing import List
import unittest

from hummingbot.core.clock import Clock, ClockMode
from hummingbot.market.idex.idex_market import IDEXMarket
from hummingbot.wallet.ethereum.ethereum_chain import EthereumChain
from hummingbot.core.event.events import (
    MarketEvent,
    WalletEvent,
    BuyOrderCompletedEvent,
    SellOrderCompletedEvent,
    WalletWrappedEthEvent,
    WalletUnwrappedEthEvent,
    BuyOrderCreatedEvent,
    SellOrderCreatedEvent,
    OrderFilledEvent,
    OrderCancelledEvent,
    TradeType,
    TradeFee
)
from hummingbot.core.event.event_logger import EventLogger
from hummingbot.market.market_base import OrderType
from hummingbot.core.data_type.order_book_tracker import OrderBookTrackerDataSourceType
from hummingbot.wallet.ethereum.web3_wallet import Web3Wallet

ETH_FXC = "ETH_FXC"

class IDEXMarketUnitTest(unittest.TestCase):
    market_events: List[MarketEvent] = [
        MarketEvent.ReceivedAsset,
        MarketEvent.BuyOrderCompleted,
        MarketEvent.SellOrderCompleted,
        MarketEvent.WithdrawAsset,
        MarketEvent.OrderFilled,
        MarketEvent.BuyOrderCreated,
        MarketEvent.SellOrderCreated,
        MarketEvent.OrderCancelled
    ]

    wallet_events: List[WalletEvent] = [
        WalletEvent.WrappedEth,
        WalletEvent.UnwrappedEth
    ]

    wallet: Web3Wallet
    market: IDEXMarket
    market_logger: EventLogger
    wallet_logger: EventLogger

    @classmethod
    def setUpClass(cls):
        cls.clock: Clock = Clock(ClockMode.REALTIME)
        cls.wallet = Web3Wallet(private_key=conf.web3_test_private_key_ddex,
                                backend_urls=conf.test_ddex_web3_provider_list,
                                erc20_token_addresses=[conf.test_ddex_erc20_token_address_1,
                                                       conf.test_ddex_erc20_token_address_2],
                                chain=EthereumChain.MAIN_NET)
        cls.market: IDEXMarket = IDEXMarket(wallet=cls.wallet,
                                            ethereum_rpc_url=conf.test_ddex_web3_provider_list[0],
                                            order_book_tracker_data_source_type=
                                            OrderBookTrackerDataSourceType.EXCHANGE_API,
                                            symbols=[ETH_FXC])
        print("Initializing IDEX market... ")
        cls.ev_loop: asyncio.BaseEventLoop = asyncio.get_event_loop()
        cls.clock.add_iterator(cls.wallet)
        cls.clock.add_iterator(cls.market)
        stack = contextlib.ExitStack()
        cls._clock = stack.enter_context(cls.clock)
        cls.ev_loop.run_until_complete(cls.wait_til_ready())
        print("Ready.")

    @classmethod
    async def wait_til_ready(cls):
        while True:
            now = time.time()
            next_iteration = now // 1.0 + 1
            if cls.market.ready:
                break
            else:
                await cls._clock.run_til(next_iteration)
            await asyncio.sleep(1.0)

    def setUp(self):
        self.market_logger = EventLogger()
        self.wallet_logger = EventLogger()
        for event_tag in self.market_events:
            self.market.add_listener(event_tag, self.market_logger)
        for event_tag in self.wallet_events:
            self.wallet.add_listener(event_tag, self.wallet_logger)

    def tearDown(self):
        for event_tag in self.market_events:
            self.market.remove_listener(event_tag, self.market_logger)
        self.market_logger = None
        for event_tag in self.wallet_events:
            self.wallet.remove_listener(event_tag, self.wallet_logger)
        self.wallet_logger = None

    async def run_parallel_async(self, *tasks):
        future: asyncio.Future = asyncio.ensure_future(asyncio.gather(*tasks))
        await self.market.start_network()
        while not future.done():
            now = time.time()
            next_iteration = now // 1.0 + 1
            await self._clock.run_til(next_iteration)
            await asyncio.sleep(1.0)
        return future.result()

    def run_parallel(self, *tasks):
        return self.ev_loop.run_until_complete(self.run_parallel_async(*tasks))

    def test_get_wallet_balances(self):
        balances = self.market.get_all_balances()
        self.assertGreaterEqual((balances["ETH"]), 0)

    def test_place_limit_buy_and_cancel(self):
        symbol = ETH_FXC
        buy_amount: float = 16000000
        buy_price = 0.00000001
        buy_order_id: str = self.market.buy(symbol, buy_amount, OrderType.LIMIT, buy_price)
        [buy_order_opened_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCreatedEvent))
        self.assertEqual(buy_order_id, buy_order_opened_event.order_id)
        self.assertEqual(buy_amount, float(buy_order_opened_event.amount))        
        self.assertEqual(ETH_FXC, buy_order_opened_event.symbol)
        self.assertEqual(OrderType.LIMIT, buy_order_opened_event.type)

        self.run_parallel(self.market.cancel_order(buy_order_id))
        [buy_order_cancelled_event] = self.run_parallel(self.market_logger.wait_for(OrderCancelledEvent))
        self.assertEqual(buy_order_opened_event.order_id, buy_order_cancelled_event.order_id)

    def test_place_limit_sell_and_cancel(self):
        symbol = ETH_FXC
        sell_amount: float = 5
        sell_price = 1
        sell_order_id: str = self.market.sell(symbol, sell_amount, OrderType.LIMIT, sell_price)
        [sell_order_opened_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCreatedEvent))
        self.assertEqual(sell_order_id, sell_order_opened_event.order_id)
        self.assertEqual(sell_amount, float(sell_order_opened_event.amount))        
        self.assertEqual(ETH_FXC, sell_order_opened_event.symbol)
        self.assertEqual(OrderType.LIMIT, sell_order_opened_event.type)

        self.run_parallel(self.market.cancel_order(sell_order_id))
        [sell_order_cancelled_event] = self.run_parallel(self.market_logger.wait_for(OrderCancelledEvent))
        self.assertEqual(sell_order_opened_event.order_id, sell_order_cancelled_event.order_id)

    def test_cancel_all_happy_case(self):
        symbol = ETH_FXC
        buy_amount: float = 16000000
        buy_price = 0.00000001
        buy_order_id: str = self.market.buy(symbol, buy_amount, OrderType.LIMIT, buy_price)
        [buy_order_opened_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCreatedEvent))
        self.assertEqual(buy_order_id, buy_order_opened_event.order_id)
        self.assertEqual(buy_amount, float(buy_order_opened_event.amount))        
        self.assertEqual(ETH_FXC, buy_order_opened_event.symbol)
        self.assertEqual(OrderType.LIMIT, buy_order_opened_event.type)
        symbol = ETH_FXC
        sell_amount: float = 5
        sell_price = 1
        sell_order_id: str = self.market.sell(symbol, sell_amount, OrderType.LIMIT, sell_price)
        [sell_order_opened_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCreatedEvent))
        self.assertEqual(sell_order_id, sell_order_opened_event.order_id)
        self.assertEqual(sell_amount, float(sell_order_opened_event.amount))        
        self.assertEqual(ETH_FXC, sell_order_opened_event.symbol)
        self.assertEqual(OrderType.LIMIT, sell_order_opened_event.type)

        [cancellation_results] = self.run_parallel(self.market.cancel_all(30))
        self.assertGreater(len(cancellation_results), 0)
        for cr in cancellation_results:
            self.assertEqual(cr.success, True)

    def test_market_buy(self):
        symbol = ETH_FXC
        buy_amount: float = 4000
        buy_order_id: str = self.market.buy(symbol, buy_amount, OrderType.MARKET)
        [order_completed_event] = self.run_parallel(self.market_logger.wait_for(BuyOrderCompletedEvent))
        order_completed_event: BuyOrderCompletedEvent = order_completed_event
        self.assertEqual(buy_order_id, order_completed_event.order_id)

    def test_market_sell(self):
        symbol = ETH_FXC
        sell_amount: float = 3600
        sell_order_id: str = self.market.sell(symbol, sell_amount, OrderType.MARKET)
        [order_completed_event] = self.run_parallel(self.market_logger.wait_for(SellOrderCompletedEvent))
        order_completed_event: SellOrderCompletedEvent = order_completed_event
        self.assertEqual(sell_order_id, order_completed_event.order_id)


def main():
    logging.basicConfig(level=logging.INFO)
    unittest.main()


if __name__ == "__main__":
    main()