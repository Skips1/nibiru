import dataclasses

import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib import pyplot as plt

from pkg import simulation as sim
from pkg import types
from pkg import stochastic

from typing import List

@dataclasses.dataclass
class ProtocolParams:
    exit_fee: float
    entry_fee: float
    frate_to_LA: int
    frate_to_IF: int
    IF_exposure_init: float
    take_profit_chance: float
    take_loss_chance: float


@dataclasses.dataclass
class LeverageAgentParams:
    num_LA_positions_per_period: float
    position_size_gamma_params: List[float]
    poisson: float


@dataclasses.dataclass
class StochasticProcessParams:
    s0: float
    mu: float
    sigma: float
    dt: float


@dataclasses.dataclass
class Parameters:
    protocol: ProtocolParams
    LA: LeverageAgentParams
    stochastic: StochasticProcessParams
    n_periods: int = 0


def get_funding_payment(protocol: types.ProtocolState, price: float,
                        bull: bool) -> float:
    """[summary]

    Args:
        protocol (types.ProtocolState): [description]
        price (float): [description]

    Returns:
        float: [description]
    """

    funding_rate_bps: int
    if bull:
        # LAs pay the IF in bull
        funding_rate_bps = protocol.frate_to_IF
        spec_amt = protocol.LA_amt
    else:
        # IF pays the LA in bear
        funding_rate_bps = protocol.frate_to_LA
        spec_amt = protocol.IF_amt

    funding_payment: float = sim.compute_funding_payment(
        bps=funding_rate_bps,
        spec=types.SpeculativeAssetState(amt=spec_amt, price_usd=price),
    )
    return funding_payment


def get_new_positions(params, row) -> pd.DataFrame:
    # Add the new positions to the protocol
    # Each position size is taken from a gamma distribution defined by the parameters
    # Each leverage is taken from a poisson distribution
    """
    New position:

    collateral_brought    |   leverage    |   entry price
    -------------------------------------------
    10k        |   5           |   4.2
    15k        |   8           |   4.2

    """

    return pd.DataFrame(
        np.vstack([
            np.random.gamma(
                *params.LA.position_size_gamma_params,
                params.LA.num_LA_positions_per_period,
            ) / row["price"],
            np.random.poisson(
                params.LA.poisson,
                params.LA.num_LA_positions_per_period,
            ),
            [row["price"]] * params.LA.num_LA_positions_per_period,
        ]).T,
        columns=["collateral_brought", "leverage", "price"],
    )


def create_scenario(params: Parameters):

    brownian = stochastic.Brownian()

    if False:
        price_dataframe = pd.DataFrame(
            zip(*[
                pd.date_range(
                    "2020-01-01", periods=params.n_periods, freq="h"),
                brownian.stock_price(
                    params.stochastic.s0,
                    params.stochastic.mu,
                    params.stochastic.sigma,
                    params.n_periods,
                    params.stochastic.dt,
                ),
            ]),
            columns=["time", "price"],
        )

    price_dataframe = pd.read_excel("data.xlsx", sheet_name="raw")
    price_dataframe["time"] = pd.to_datetime(price_dataframe.Date)
    price_dataframe["price"] = price_dataframe["Luna"]

    price_dataframe = (price_dataframe[price_dataframe.price.notnull()][[
        "time", "price"
    ]].sort_values("time").reset_index(drop=True))

    params.n_periods = price_dataframe.shape[0]

    current_positions = pd.DataFrame(
        columns=["collateral_brought", "leverage", "price"])

    insurance_fund = params.protocol.IF_exposure_init

    previous_price = -1
    for i, row in price_dataframe.iterrows():

        new_positions = get_new_positions(params=params, row=row)

        current_positions = pd.concat([current_positions, new_positions])

        # Compute all exits
        liquidations = (
            current_positions.collateral_brought * current_positions.leverage *
            (row["price"] - current_positions.price) +
            current_positions.collateral_brought * row["price"] < 0)

        exits_loss = (row["price"] - current_positions.price <
                      0) & (np.random.random(len(current_positions)) <
                            params.protocol.take_loss_chance)

        exits_profit = (row["price"] - current_positions.price >
                        0) & (np.random.random(len(current_positions)) <
                              params.protocol.take_profit_chance)

        entry_fee = (new_positions[["collateral_brought", "leverage"]].product(
            axis=1).sum() * params.protocol.entry_fee) * row["price"]

        exit_fee = (current_positions.loc[
            (liquidations | exits_loss | exits_profit),
            ["collateral_brought", "leverage"], ].product(axis=1).sum() *
                    params.protocol.exit_fee) * row["price"]

        price_dataframe.loc[i, "fees"] = entry_fee + exit_fee
        insurance_fund += entry_fee + exit_fee

        # remove exits from active positions
        current_positions = current_positions[~(liquidations | exits_loss
                                                | exits_profit)].copy()

        # funding rate calculation TODO: Update this to new logic

        # TODO: Create a prootocol state and call the function
        positive_unrealized_pnl = current_positions.price > row["price"]
        negative_unrealized_pnl = current_positions.price < row["price"]

        LA_amt = (current_positions[["collateral_brought", "leverage"
                                     ]].product(axis=1).sum() * row["price"])
        protocol = types.ProtocolState(
            LA_amt=LA_amt,
            IF_amt=insurance_fund,
            frate_to_LA=params.protocol.frate_to_LA,
            frate_to_IF=params.protocol.frate_to_IF,
            IA_amt=0,
        )

        bull: bool = row["price"] > previous_price
        funding_payment = get_funding_payment(
            protocol=protocol, price=row["price"], bull=bull)

        # TODO: Are we allowed to take the money from the LAs collaterals?
        # We assume that they pay it from an infinite wallet for now
        if bull:
            insurance_fund += funding_payment
        else:
            insurance_fund -= funding_payment

        price_dataframe.loc[i, "treasury"] = insurance_fund

        price_dataframe.loc[i, "entry_fee_income"] = entry_fee * row["price"]
        price_dataframe.loc[i, "exit_fee_income"] = exit_fee * row["price"]

        price_dataframe.loc[i, "LA_exposure"] = (
            current_positions.collateral_brought  # 10_000
            # * current_positions.leverage  # 10
            * row["price"]).sum()
        price_dataframe.loc[i, "LA_position"] = (
            current_positions.collateral_brought  # 10_000
            * current_positions.leverage  # 10
            * row["price"]).sum()

        price_dataframe.loc[i, "funding_payments"] = funding_payment
        price_dataframe.loc[i, "bull"] = bull
        price_dataframe.loc[i, "liquidations"] = liquidations.sum()
        price_dataframe.loc[i, "exits"] = (exits_profit | exits_loss).sum()

        previous_price = row["price"]

    return price_dataframe