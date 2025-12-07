"""
CDL Environment Integration Tests
Contextual Dynamics Laboratory, Dartmouth College

These tests verify that the CDL conda environment is properly configured
and all packages are working correctly.

To run these tests:
    1. Activate the CDL environment: conda activate cdl
    2. Run pytest: pytest tests/test_cdl_environment.py -v

IMPORTANT: These tests use REAL API calls and imports - no mocks or simulations.
"""

import pytest
import sys
import platform


class TestCorePackages:
    """Test that core scientific computing packages are installed and functional."""

    def test_numpy_import(self):
        """Test that NumPy can be imported and has a version."""
        import numpy as np
        assert np.__version__ is not None
        print(f"numpy version: {np.__version__}")

    def test_numpy_functionality(self):
        """Test basic NumPy operations."""
        import numpy as np
        arr = np.array([1, 2, 3, 4, 5])
        assert arr.sum() == 15
        assert arr.mean() == 3.0
        assert arr.shape == (5,)

    def test_scipy_import(self):
        """Test that SciPy can be imported and has a version."""
        import scipy
        assert scipy.__version__ is not None
        print(f"scipy version: {scipy.__version__}")

    def test_scipy_functionality(self):
        """Test basic SciPy operations."""
        from scipy import stats
        import numpy as np
        data = np.random.randn(100)
        result = stats.describe(data)
        assert result.nobs == 100

    def test_pandas_import(self):
        """Test that Pandas can be imported and has a version."""
        import pandas as pd
        assert pd.__version__ is not None
        print(f"pandas version: {pd.__version__}")

    def test_pandas_functionality(self):
        """Test basic Pandas operations."""
        import pandas as pd
        df = pd.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})
        assert len(df) == 3
        assert list(df.columns) == ['a', 'b']
        assert df['a'].sum() == 6

    def test_polars_import(self):
        """Test that Polars can be imported and has a version."""
        import polars as pl
        assert pl.__version__ is not None
        print(f"polars version: {pl.__version__}")

    def test_polars_functionality(self):
        """Test basic Polars operations."""
        import polars as pl
        df = pl.DataFrame({'a': [1, 2, 3], 'b': [4, 5, 6]})
        assert len(df) == 3
        assert df['a'].sum() == 6


class TestVisualization:
    """Test visualization packages."""

    def test_matplotlib_import(self):
        """Test that Matplotlib can be imported and has a version."""
        import matplotlib
        assert matplotlib.__version__ is not None
        print(f"matplotlib version: {matplotlib.__version__}")

    def test_matplotlib_functionality(self):
        """Test basic Matplotlib figure creation."""
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend for testing
        import matplotlib.pyplot as plt
        import numpy as np

        fig, ax = plt.subplots()
        x = np.linspace(0, 10, 100)
        ax.plot(x, np.sin(x))
        assert fig is not None
        plt.close(fig)

    def test_seaborn_import(self):
        """Test that Seaborn can be imported and has a version."""
        import seaborn as sns
        assert sns.__version__ is not None
        print(f"seaborn version: {sns.__version__}")


class TestMachineLearning:
    """Test machine learning packages."""

    def test_sklearn_import(self):
        """Test that scikit-learn can be imported and has a version."""
        import sklearn
        assert sklearn.__version__ is not None
        print(f"scikit-learn version: {sklearn.__version__}")

    def test_sklearn_functionality(self):
        """Test basic scikit-learn classifier."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.datasets import make_classification
        import numpy as np

        X, y = make_classification(n_samples=100, n_features=10, random_state=42)
        clf = LogisticRegression(random_state=42, max_iter=1000)
        clf.fit(X, y)
        predictions = clf.predict(X)
        assert len(predictions) == 100
        assert clf.score(X, y) > 0.5

    def test_umap_import(self):
        """Test that UMAP can be imported."""
        import umap
        assert umap is not None

    def test_umap_functionality(self):
        """Test basic UMAP dimensionality reduction."""
        import umap
        import numpy as np

        data = np.random.randn(50, 10)
        reducer = umap.UMAP(n_components=2, random_state=42, n_neighbors=5)
        embedding = reducer.fit_transform(data)
        assert embedding.shape == (50, 2)


class TestPyTorch:
    """Test PyTorch installation and basic functionality."""

    def test_pytorch_import(self):
        """Test that PyTorch can be imported and has a version."""
        import torch
        assert torch.__version__ is not None
        print(f"pytorch version: {torch.__version__}")

    def test_pytorch_tensor_operations(self):
        """Test basic PyTorch tensor operations."""
        import torch

        x = torch.tensor([1.0, 2.0, 3.0])
        y = torch.tensor([4.0, 5.0, 6.0])
        z = x + y
        assert torch.allclose(z, torch.tensor([5.0, 7.0, 9.0]))

    def test_pytorch_cuda_or_mps(self):
        """Test PyTorch device availability (CUDA, MPS, or CPU)."""
        import torch

        if torch.cuda.is_available():
            print(f"CUDA available: {torch.cuda.device_count()} device(s)")
            device = torch.device("cuda")
        elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            print("MPS (Apple Silicon) available")
            device = torch.device("mps")
        else:
            print("CPU only")
            device = torch.device("cpu")

        # Test tensor on device
        x = torch.tensor([1.0, 2.0, 3.0], device=device)
        assert x.device.type == device.type

    def test_pytorch_neural_network(self):
        """Test basic PyTorch neural network."""
        import torch
        import torch.nn as nn

        model = nn.Sequential(
            nn.Linear(10, 5),
            nn.ReLU(),
            nn.Linear(5, 2)
        )

        x = torch.randn(1, 10)
        output = model(x)
        assert output.shape == (1, 2)


class TestTransformers:
    """Test Hugging Face Transformers installation."""

    def test_transformers_import(self):
        """Test that Transformers can be imported and has a version."""
        import transformers
        assert transformers.__version__ is not None
        print(f"transformers version: {transformers.__version__}")

    def test_datasets_import(self):
        """Test that datasets can be imported and has a version."""
        import datasets
        assert datasets.__version__ is not None
        print(f"datasets version: {datasets.__version__}")

    def test_huggingface_hub_import(self):
        """Test that huggingface_hub can be imported."""
        import huggingface_hub
        assert huggingface_hub.__version__ is not None
        print(f"huggingface_hub version: {huggingface_hub.__version__}")

    @pytest.mark.slow
    def test_transformers_model_load(self):
        """Test loading a tiny model from Hugging Face.

        This test makes real network calls to Hugging Face.
        Mark with @pytest.mark.slow to skip in quick test runs.
        """
        from transformers import pipeline

        # Use a tiny model for fast testing
        pipe = pipeline("text-generation", model="sshleifer/tiny-gpt2")
        result = pipe("Hello", max_length=10, num_return_sequences=1)
        assert len(result) > 0
        assert "generated_text" in result[0]


class TestHuggingFaceContextLab:
    """Test access to ContextLab Hugging Face resources."""

    @pytest.mark.slow
    def test_contextlab_dataset_access(self):
        """Test loading a ContextLab dataset from Hugging Face.

        This test makes real network calls to Hugging Face.
        """
        from datasets import load_dataset

        # Load a small subset of the austen-corpus
        ds = load_dataset("contextlab/austen-corpus", split="train[:10]")
        assert len(ds) == 10
        print(f"Loaded {len(ds)} samples from contextlab/austen-corpus")


class TestCDLPackages:
    """Test CDL-specific packages."""

    def test_hypertools_import(self):
        """Test that HyperTools can be imported."""
        import hypertools as hyp
        assert hyp.__version__ is not None
        print(f"hypertools version: {hyp.__version__}")

    def test_hypertools_functionality(self):
        """Test basic HyperTools operations."""
        import hypertools as hyp
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')

        # Create some test data
        data = np.random.randn(100, 10)

        # Test reduce
        reduced = hyp.reduce(data, ndims=3)
        assert reduced.shape == (100, 3)

    @pytest.mark.slow
    def test_hypertools_plot(self):
        """Test HyperTools plotting (non-interactive)."""
        import hypertools as hyp
        import numpy as np
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        data = np.random.randn(100, 10)
        geo = hyp.plot(data, show=False)
        assert geo is not None
        plt.close('all')

    def test_quail_import(self):
        """Test that Quail can be imported."""
        import quail
        assert quail.__version__ is not None
        print(f"quail version: {quail.__version__}")

    def test_timecorr_import(self):
        """Test that timecorr can be imported."""
        import timecorr as tc
        assert tc is not None

    def test_timecorr_functionality(self):
        """Test basic timecorr operations."""
        import timecorr as tc
        import numpy as np

        # Create synthetic timeseries data
        data = np.random.randn(50, 10)  # 50 timepoints, 10 features

        # Run timecorr with simple settings
        result = tc.timecorr(data, weights_function=tc.gaussian_weights, weights_params={'var': 5})
        assert result is not None

    def test_supereeg_import(self):
        """Test that supereeg can be imported."""
        import supereeg as se
        assert se is not None


class TestNeuroimaging:
    """Test neuroimaging packages."""

    def test_nibabel_import(self):
        """Test that nibabel can be imported."""
        import nibabel as nib
        assert nib.__version__ is not None
        print(f"nibabel version: {nib.__version__}")

    def test_nilearn_import(self):
        """Test that nilearn can be imported."""
        import nilearn
        assert nilearn.__version__ is not None
        print(f"nilearn version: {nilearn.__version__}")


class TestJupyter:
    """Test Jupyter packages."""

    def test_jupyter_import(self):
        """Test that Jupyter packages can be imported."""
        import jupyter_core
        assert jupyter_core.__version__ is not None
        print(f"jupyter_core version: {jupyter_core.__version__}")

    def test_ipykernel_import(self):
        """Test that ipykernel can be imported."""
        import ipykernel
        assert ipykernel.__version__ is not None
        print(f"ipykernel version: {ipykernel.__version__}")


class TestUtilities:
    """Test utility packages."""

    def test_requests_import(self):
        """Test that requests can be imported."""
        import requests
        assert requests.__version__ is not None
        print(f"requests version: {requests.__version__}")

    def test_tqdm_import(self):
        """Test that tqdm can be imported."""
        import tqdm
        assert tqdm.__version__ is not None
        print(f"tqdm version: {tqdm.__version__}")

    def test_joblib_import(self):
        """Test that joblib can be imported."""
        import joblib
        assert joblib.__version__ is not None
        print(f"joblib version: {joblib.__version__}")

    def test_h5py_import(self):
        """Test that h5py can be imported."""
        import h5py
        assert h5py.__version__ is not None
        print(f"h5py version: {h5py.__version__}")

    def test_deepdish_import(self):
        """Test that deepdish can be imported."""
        import deepdish as dd
        assert dd is not None


class TestEnvironmentInfo:
    """Print environment information for debugging."""

    def test_python_version(self):
        """Print Python version."""
        print(f"Python version: {sys.version}")
        assert sys.version_info >= (3, 9)

    def test_platform_info(self):
        """Print platform information."""
        print(f"Platform: {platform.platform()}")
        print(f"Processor: {platform.processor()}")
        print(f"Machine: {platform.machine()}")


# Custom pytest marker for slow tests
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
