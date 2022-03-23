package dex_test

import (
	"testing"

	keepertest "github.com/MatrixDao/matrix/testutil/keeper"
	"github.com/MatrixDao/matrix/testutil/nullify"
	"github.com/MatrixDao/matrix/x/dex"
	"github.com/MatrixDao/matrix/x/dex/types"
	"github.com/stretchr/testify/require"
)

func TestGenesis(t *testing.T) {
	genesisState := types.GenesisState{
		Params: types.DefaultParams(),
	}

	k, ctx, _, _ := keepertest.DexKeeper(t)
	dex.InitGenesis(ctx, *k, genesisState)
	got := dex.ExportGenesis(ctx, *k)
	require.NotNil(t, got)

	nullify.Fill(&genesisState)
	nullify.Fill(got)

	require.Equal(t, genesisState, *got)
}
